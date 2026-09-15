"""
DhanSyncService - imports the Dhan account's trades and holdings into the
Risk tab's positions. Read-only: nothing is ever sent to Dhan but GETs.

One sync, on a background thread (single-flight):
1. check the token (profile) and renew it once it has less than
   RENEW_WITHIN left - Dhan tokens last 24h and only an active one can be
   renewed, so syncing (or `keep_alive`) at least daily keeps it valid without
   pasting a new one;
2. read trade history for the days not read yet, from the start date the
   user picks (default: `broker_history_days` back) - picking an earlier date
   later reads just the missing stretch - and today's trade book; store fills;
3. read holdings, then rebuild open and closed positions from every stored
   fill (domain/broker.py) and upsert them by their stable ref: Dhan owns
   quantity, prices, dates and charges; the user's stop, plan and note stay.

A manual position sized in the Risk tab and bought on Dhan a few days either
side becomes the imported row, keeping its plan - so a bigger fill shows as
oversized. A removed row comes back on the next sync; a hidden one doesn't
until the user brings hidden rows back. When an open position's ref changes
(e.g. an earlier start date adds older buys to it), its stop, plan and note
move to the new row rather than being lost.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from dataclasses import dataclass, replace

from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.broker_trades import BrokerTradeRepository
from swingdash.adapters.storage.repos.positions import PositionRepository
from swingdash.domain.broker import BrokerHolding, BrokerTrade, ImportedPosition, reconcile
from swingdash.domain.errors import BrokerAuthError
from swingdash.domain.risk.portfolio import Position
from swingdash.services.broker_credentials import BrokerCredentialsStore
from swingdash.services.instruments import InstrumentService, InstrumentsUnavailableError
from swingdash.services.ports import BrokerSourcePort, MarketCalendar
from swingdash.services.risk import RiskService

logger = logging.getLogger(__name__)

BROKER = "dhan"
# Renew a token once it's a few hours old: a daily sync at a slightly later
# time than yesterday's must still find it active.
RENEW_WITHIN = dt.timedelta(hours=20)
# A plan taken in the Risk tab links to a Dhan buy this many days either side.
PLAN_LINK_DAYS = 5
_EVENT_LOG_SIZE = 20

_HISTORY_FROM = "dhan_history_from"  # the start date positions are built from
_HISTORY_COVERED_FROM = "dhan_history_covered_from"  # the earliest day ever read
_HISTORY_THROUGH = "dhan_history_through"
_LAST_SYNC = "dhan_last_sync"
_ACCOUNT_NAME = "dhan_account_name"
_TOKEN_VALID_UNTIL = "dhan_token_valid_until"


@dataclass(frozen=True)
class SyncStatus:
    connected: bool  # credentials are set
    running: bool
    phase: str
    last_sync: dt.datetime | None
    error: str | None
    needs_token: bool  # Dhan rejected the token: paste a new one
    account_name: str
    token_valid_until: dt.datetime | None
    mismatches: tuple[str, ...]


class DhanSyncService:
    def __init__(
        self,
        source: BrokerSourcePort,
        credentials: BrokerCredentialsStore,
        *,
        trades: BrokerTradeRepository,
        positions: PositionRepository,
        state: AppStateRepository,
        instruments: InstrumentService,
        calendar: MarketCalendar,
        risk: RiskService,
    ) -> None:
        self._source = source
        self._credentials = credentials
        self._trades = trades
        self._positions = positions
        self._state = state
        self._instruments = instruments
        self._calendar = calendar
        self._risk = risk

        self.version = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._phase = ""
        self._error: str | None = None
        self._needs_token = False
        self._mismatches: tuple[str, ...] = ()
        self._events: deque[str] = deque(maxlen=_EVENT_LOG_SIZE)

    # --- UI-facing -----------------------------------------------------------

    @property
    def status(self) -> SyncStatus:
        return SyncStatus(
            connected=self._credentials.current() is not None,
            running=self.running,
            phase=self._phase,
            last_sync=self._datetime(_LAST_SYNC),
            error=self._error,
            needs_token=self._needs_token,
            account_name=self._state.get(_ACCOUNT_NAME) or "",
            token_valid_until=self._datetime(_TOKEN_VALID_UNTIL),
            mismatches=self._mismatches,
        )

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def client_id(self) -> str:
        credentials = self._credentials.current()
        return credentials.client_id if credentials else ""

    def connect(
        self,
        client_id: str,
        access_token: str,
        history_from: dt.date | None = None,
        *,
        restore_hidden: bool = False,
    ) -> bool:
        """Store new credentials and sync with them. Returns whether a sync started."""
        if not client_id.strip() or not access_token.strip():
            raise ValueError("Enter both the Dhan client ID and the access token.")
        self._credentials.save(client_id, access_token)
        self._needs_token = False
        self._error = None
        self.version += 1
        return self.sync(history_from, restore_hidden=restore_hidden)

    def history_from(self) -> dt.date:
        """The start date the last sync used, or the default for a first one."""
        stored = self._date(_HISTORY_FROM)
        if stored is not None:
            return stored
        return self._calendar.today() - dt.timedelta(days=self._risk.settings.broker_history_days)

    def hidden_count(self) -> int:
        return len(self._positions.ignored(BROKER))

    def disconnect(self) -> None:
        """Forget the token. Imported positions stay."""
        self._credentials.clear()
        self.version += 1

    def sync(self, history_from: dt.date | None = None, *, restore_hidden: bool = False) -> bool:
        """
        Start a sync in the background; False if one is running or nothing is
        connected. `history_from`: build positions from trades since this day
        (default: the last sync's). `restore_hidden`: bring hidden rows back.
        """
        if history_from is not None and history_from >= self._calendar.today():
            raise ValueError("The start date must be before today.")
        with self._lock:
            if self.running or self._credentials.current() is None:
                return False
            self._thread = threading.Thread(
                target=self._run,
                args=(history_from, restore_hidden),
                name="dhan-sync",
                daemon=True,
            )
            self._thread.start()
        return True

    def sync_if_due(self) -> bool:
        """At most one automatic sync a day, and never with a token Dhan rejected."""
        last = self.status.last_sync
        if self._needs_token or (last is not None and last.date() == self._calendar.today()):
            return False
        return self.sync()

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def events(self) -> list[str]:
        drained = list(self._events)
        self._events.clear()
        return drained

    # --- the sync ------------------------------------------------------------------

    def _run(self, history_from: dt.date | None, restore_hidden: bool) -> None:
        self._error = None
        try:
            if restore_hidden:
                restored = self._positions.unignore_all(BROKER)
                if restored:
                    self._events.append(f"bringing back {restored} hidden position(s)")
            self._sync(history_from)
        except BrokerAuthError as exc:
            self._needs_token = True
            self._error = str(exc)
            self._events.append(str(exc))
        except Exception as exc:  # the adapter's errors carry user-facing messages
            logger.warning("dhan sync failed", exc_info=True)
            self._error = f"Dhan sync failed: {exc}"
            self._events.append(self._error)
        finally:
            self._phase = ""
            self.version += 1

    def _sync(self, requested_from: dt.date | None) -> None:
        today = self._calendar.today()
        self._set_phase("checking the Dhan account")
        account = self._source.account()
        valid_until = account.token_valid_until
        if valid_until is not None and valid_until - self._calendar.now() < RENEW_WITHIN:
            self._set_phase("renewing the Dhan token")
            renewed = self._source.renew_token()
            self._credentials.save(account.client_id, renewed.token)
            valid_until = renewed.valid_until or valid_until
            if valid_until:
                self._events.append(f"Dhan token renewed until {valid_until:%d %b %H:%M}")
        self._needs_token = False
        self._state.set(_ACCOUNT_NAME, account.name)
        if valid_until is not None:
            self._state.set(_TOKEN_VALID_UNTIL, valid_until.isoformat())

        history_from = requested_from or self.history_from()
        yesterday = today - dt.timedelta(days=1)
        for start, end in self._missing_history(history_from, yesterday):
            self._set_phase(f"reading Dhan trades {start:%d %b %Y} - {end:%d %b %Y}")
            self._trades.save(BROKER, self._source.trade_history(start, end))
        covered = self._date(_HISTORY_COVERED_FROM)
        if covered is None or history_from < covered:
            self._state.set(_HISTORY_COVERED_FROM, history_from.isoformat())
        self._state.set(_HISTORY_THROUGH, yesterday.isoformat())
        self._state.set(_HISTORY_FROM, history_from.isoformat())
        self._set_phase("reading today's Dhan trades and holdings")
        self._trades.save(BROKER, self._source.trades_today())
        holdings = self._source.holdings()

        self._set_phase("rebuilding positions")
        imported, closed = self._apply(history_from, today, holdings)
        self._state.set(_LAST_SYNC, self._calendar.now().isoformat())
        self._events.append(
            f"Dhan synced: {imported} open, {closed} closed"
            + (f" - {len(self._mismatches)} to check" if self._mismatches else "")
        )

    def _apply(
        self, history_from: dt.date, today: dt.date, holdings: list[BrokerHolding]
    ) -> tuple[int, int]:
        fills: list[BrokerTrade] = []
        unnamed: set[str] = set()
        for fill in self._trades.all(BROKER):
            if fill.time.date() < history_from:
                continue  # before the chosen start: holdings explain what's still held
            symbol = self._nse_symbol(fill.isin, fill.symbol)
            if symbol is None:
                unnamed.add(fill.isin or fill.symbol or fill.trade_id)
                continue
            fills.append(replace(fill, symbol=symbol))
        named_holdings = [
            replace(h, symbol=self._nse_symbol(h.isin, h.symbol) or h.symbol) for h in holdings
        ]

        result = reconcile(fills, named_holdings, history_from=history_from, today=today)
        ignored = self._positions.ignored(BROKER)
        wanted = [p for p in result.positions if p.ref not in ignored]
        existing = self._positions.by_ref(BROKER)
        plans = [
            p
            for p in self._positions.all()
            if p.is_open and not p.is_imported and p.planned_quantity is not None
        ]
        wanted_refs = {p.ref for p in wanted}
        # Open rows whose ref no longer comes up - their stop and note can move on.
        stale_open = [p for ref, p in existing.items() if ref not in wanted_refs and p.is_open]
        for position in wanted:
            into = None
            if position.ref not in existing and position.closed_on is None:
                previous = next(
                    (
                        p
                        for p in stale_open
                        if p.symbol == position.symbol and p.funding == position.funding
                    ),
                    None,
                )
                plan = _matching_plan(position, plans)
                if previous is not None:
                    into = previous.id
                    stale_open.remove(previous)
                elif plan is not None:
                    into = plan.id
                    plans.remove(plan)
                    self._events.append(f"{position.symbol}: linked the Dhan buy to your plan")
            self._positions.save_imported(
                BROKER, position, self._instrument_key(position.symbol), into=into
            )
        still_stale = {p.broker_ref for p in stale_open if p.broker_ref}
        self._positions.delete_refs(
            BROKER,
            {ref for ref, p in existing.items() if ref not in wanted_refs and not p.is_open}
            | still_stale,
        )

        self._mismatches = result.mismatches + tuple(
            f"{name}: a Dhan trade swingdash can't match to an NSE stock"
            for name in sorted(unnamed)
        )
        self._risk.reload()
        return (
            sum(1 for p in wanted if p.closed_on is None),
            sum(1 for p in wanted if p.closed_on is not None),
        )

    # --- helpers -------------------------------------------------------------------

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self.version += 1

    def _missing_history(self, start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
        """The day ranges between `start` and `end` that haven't been read yet."""
        covered = self._date(_HISTORY_COVERED_FROM)
        through = self._date(_HISTORY_THROUGH)
        if covered is None or through is None:
            return [(start, end)] if start <= end else []
        gaps: list[tuple[dt.date, dt.date]] = []
        if start < covered:
            gaps.append((start, min(end, covered - dt.timedelta(days=1))))
        if through < end:
            gaps.append((max(start, through + dt.timedelta(days=1)), end))
        return [(a, b) for a, b in gaps if a <= b]

    def _nse_symbol(self, isin: str | None, trading_symbol: str) -> str | None:
        """Trade history names stocks by ISIN; the trade book and holdings by symbol."""
        try:
            if isin:
                found = self._instruments.find_symbol_by_isin(isin)
                if found:
                    return found
            if trading_symbol and self._instruments.find_instrument_key(trading_symbol):
                return trading_symbol.strip().upper()
        except InstrumentsUnavailableError:
            return trading_symbol.strip().upper() or None
        return None

    def _instrument_key(self, symbol: str) -> str | None:
        try:
            return self._instruments.find_instrument_key(symbol)
        except InstrumentsUnavailableError:
            return None

    def _date(self, key: str) -> dt.date | None:
        raw = self._state.get(key)
        return dt.date.fromisoformat(raw) if raw else None

    def _datetime(self, key: str) -> dt.datetime | None:
        raw = self._state.get(key)
        return dt.datetime.fromisoformat(raw) if raw else None


def _matching_plan(imported: ImportedPosition, plans: list[Position]) -> Position | None:
    """The open, sized manual position for the same stock taken closest to the Dhan buy."""
    candidates = [
        plan
        for plan in plans
        if plan.symbol == imported.symbol
        and abs((plan.opened_on - imported.opened_on).days) <= PLAN_LINK_DAYS
    ]
    return min(
        candidates, key=lambda plan: abs((plan.opened_on - imported.opened_on).days), default=None
    )
