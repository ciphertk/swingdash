"""
RiskService - position sizing and the open-risk tracker behind the Risk tab.

- Capital, risk per trade and limits come from RiskSettingsService.
  Positions are entered by hand (the Upstox Analytics Token can't read
  holdings) or imported from a broker (DhanSyncService); both live in SQLite.
- Each open position's risk is measured to its stop - or, with no stop or the
  price already below it, to an assumed stop below the price - and its breaches
  of plan are listed (domain/risk/discipline.py).
- Prices: the live feed (one hub subscription for open positions plus the
  symbol being sized) or, when it has nothing newer, the Market Quote API's
  last traded price (refreshed at most once a minute, off the UI thread -
  it also answers after the close, when the feed may send nothing). The last
  cached daily close is only a last resort: history never includes today.
- Stops by ATR / recent low and the liquidity check need daily candles: read
  from the cache, topped up once per session on a background thread (paced by
  the shared Upstox limiter).
- Pull, don't push: ticks store a price; the tab polls `portfolio()` and asks
  `size()` as the form changes.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from swingdash.adapters.storage.repos.positions import PositionRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.risk.checks import RiskWarning, TradeContext, trade_warnings
from swingdash.domain.risk.discipline import PositionRisk, position_risk
from swingdash.domain.risk.portfolio import PortfolioSummary, Position, summarise
from swingdash.domain.risk.sizing import RiskSpec, SizingInput, SizingResult, size_position
from swingdash.domain.risk.stops import RiskInputError, StopMethod, atr, resolve_stop
from swingdash.domain.securities import NOT_UNDER_SURVEILLANCE, PriceBand, Surveillance
from swingdash.services.candles import CandleService
from swingdash.services.instruments import InstrumentService, InstrumentsUnavailableError
from swingdash.services.market_data_hub import MarketDataHub, Subscription
from swingdash.services.ports import MarketCalendar, QuoteSource
from swingdash.services.risk_settings import RiskSettings, RiskSettingsService
from swingdash.services.securities import SecuritiesService

logger = logging.getLogger(__name__)

# Sessions of history the checks need: 20-day average volume.
_VOLUME_SESSIONS = 20
_EVENT_LOG_SIZE = 20
# How often to ask the Market Quote API for symbols the feed isn't updating.
QUOTE_REFRESH_SECONDS = 60.0

PriceSource = Literal["live", "quote", "close"]


@dataclass(frozen=True)
class Quote:
    price: float
    # live: a feed tick; quote: the Market Quote API's last traded price;
    # close: the last cached daily close (not today's).
    source: PriceSource
    as_of: str | None = None  # the quote's time, or the close's date

    @property
    def live(self) -> bool:
        return self.source == "live"


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    instrument_key: str
    quote: Quote | None
    bars: tuple[DailyBar, ...]
    history_pending: bool  # candles still being fetched
    band: PriceBand | None
    surveillance: Surveillance
    series: str | None
    lot_size: int
    avg_volume: float | None


@dataclass(frozen=True)
class SizedTrade:
    info: SymbolInfo
    stop: float
    result: SizingResult
    warnings: tuple[RiskWarning, ...]


@dataclass(frozen=True)
class PositionRow:
    position: Position
    quote: Quote | None
    days_held: int
    risk: PositionRisk


@dataclass(frozen=True)
class PortfolioView:
    summary: PortfolioSummary
    open: tuple[PositionRow, ...]
    closed: tuple[Position, ...]


class RiskService:
    def __init__(
        self,
        repo: PositionRepository,
        settings: RiskSettingsService,
        *,
        instruments: InstrumentService,
        candles: CandleService,
        securities: SecuritiesService,
        calendar: MarketCalendar,
        hub: MarketDataHub,
        quotes: QuoteSource,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._instruments = instruments
        self._candles = candles
        self._securities = securities
        self._calendar = calendar
        self._hub = hub
        self._quotes = quotes

        self.version = 0  # bumped when positions, settings or fetched history change
        self._positions: list[Position] | None = None
        # key -> (price, monotonic time). Ticks are written by the feed thread,
        # quotes by a worker; each write is a single dict assignment.
        self._ticks: dict[str, tuple[float, float]] = {}
        self._rest: dict[str, tuple[float, float, str]] = {}  # ... plus "HH:MM"
        self._quote_attempts: dict[str, float] = {}
        self._quotes_in_flight = False
        self._quote_error: str | None = None
        self._watched_key: str | None = None
        self._subscription: Subscription | None = None
        self._subscribed: frozenset[str] = frozenset()

        self._lock = threading.Lock()
        self._fetching: set[str] = set()
        self._attempted: set[tuple[str, dt.date]] = set()
        self._bars: dict[str, tuple[DailyBar, ...]] = {}  # cached candles, for today
        self._bars_day: dt.date | None = None
        self._events: deque[str] = deque(maxlen=_EVENT_LOG_SIZE)
        self._flags: dict[str, tuple[PriceBand | None, Surveillance]] = {}
        self._flags_version = -1

    # --- settings ----------------------------------------------------------

    @property
    def settings(self) -> RiskSettings:
        return self._settings.get()

    def save_settings(self, settings: RiskSettings) -> None:
        self._settings.save(settings)
        self._bars.clear()  # the history window depends on the settings
        self.version += 1

    # --- symbols -----------------------------------------------------------

    def symbols(self) -> list[str]:
        """Every NSE stock symbol, for autocomplete."""
        try:
            return sorted(e["trading_symbol"] for e in self._instruments.list_equities())
        except InstrumentsUnavailableError:
            return []

    def watch(self, symbol: str | None) -> None:
        """The symbol being sized: stream its price and top up its history."""
        key = self._key(symbol) if symbol else None
        if key == self._watched_key:
            return
        self._watched_key = key
        self._resubscribe()

    def info(self, symbol: str) -> SymbolInfo | None:
        """None if it isn't an NSE stock swingdash knows."""
        key = self._key(symbol)
        if key is None:
            return None
        symbol = symbol.strip().upper()
        bars = self._history(key)
        self._refresh_quotes([key])
        band, surveillance = self._flags_for(symbol)
        recent = bars[-_VOLUME_SESSIONS:]
        return SymbolInfo(
            symbol=symbol,
            instrument_key=key,
            quote=self._quote(key, bars),
            bars=bars,
            history_pending=key in self._fetching,
            band=band,
            surveillance=surveillance,
            series=self._instruments.find_series(symbol),
            lot_size=self._instruments.lot_size(symbol),
            avg_volume=sum(b.volume for b in recent) / len(recent) if recent else None,
        )

    # --- sizing ------------------------------------------------------------

    def size(
        self,
        symbol: str,
        entry: float,
        stop_method: StopMethod,
        stop_value: float,
        risk: RiskSpec | None = None,
    ) -> SizedTrade:
        """Raises RiskInputError with a message fit to show the user."""
        info = self.info(symbol)
        if info is None:
            raise RiskInputError(f"'{symbol.strip().upper()}' isn't an NSE stock swingdash knows.")
        settings = self.settings
        stop = resolve_stop(
            stop_method, entry, stop_value, info.bars, atr_period=settings.atr_period
        )
        summary = self.portfolio().summary
        result = size_position(
            SizingInput(
                capital=settings.capital,
                risk=risk or settings.risk,
                entry=entry,
                stop=stop,
                max_allocation_pct=settings.max_allocation_pct,
                free_capital=summary.free_capital,
                heat_left=summary.heat_left,
                lot_size=info.lot_size,
                charges=settings.charges,
            )
        )
        context = TradeContext(
            band=info.band,
            surveillance=info.surveillance,
            series=info.series,
            avg_volume=info.avg_volume,
            liquidity_warn_pct=settings.liquidity_warn_pct,
        )
        return SizedTrade(info, stop, result, tuple(trade_warnings(result, context)))

    # --- positions ---------------------------------------------------------

    def positions(self) -> list[Position]:
        if self._positions is None:
            self._positions = self._repo.all()
        return list(self._positions)

    def portfolio(self) -> PortfolioView:
        positions = self.positions()
        today = self._calendar.today()
        self._refresh_quotes(
            [p.instrument_key for p in positions if p.is_open and p.instrument_key]
        )
        settings = self.settings
        assumed = settings.assumed_stop
        rows: list[PositionRow] = []
        for p in positions:
            if not p.is_open:
                continue
            bars = self._history(p.instrument_key) if p.instrument_key else ()
            quote = self._quote(p.instrument_key, bars) if p.instrument_key else None
            price = quote.price if quote else None
            risk = position_risk(p, price, atr(bars, settings.atr_period), assumed)
            rows.append(PositionRow(p, quote, (today - p.opened_on).days, risk))
        summary = summarise(
            positions,
            settings.capital,
            settings.max_heat_pct,
            risks={row.position.id: row.risk for row in rows},
            prices={row.position.id: row.quote.price for row in rows if row.quote},
        )
        return PortfolioView(
            summary=summary,
            open=tuple(rows),
            closed=tuple(p for p in positions if not p.is_open),
        )

    def reload(self) -> None:
        """Positions changed outside this service (a broker sync)."""
        self._changed()

    def open_position(
        self,
        symbol: str,
        quantity: int,
        entry: float,
        stop: float | None,
        note: str = "",
        opened_on: dt.date | None = None,
        *,
        planned: bool = True,
    ) -> Position:
        """
        `opened_on`: the day the trade was taken (default today). `stop` None:
        no stop-loss (flagged). `planned`: it was sized in the tab, so its
        quantity and stop are the plan later imports are compared with.
        """
        key = self._key(symbol)
        if key is None:
            raise RiskInputError(f"'{symbol.strip().upper()}' isn't an NSE stock swingdash knows.")
        _check_position(quantity, entry, stop)
        if stop is not None and stop >= entry:
            raise RiskInputError("The stop must be below entry when opening a position.")
        opened_on = opened_on or self._calendar.today()
        self._check_not_future(opened_on, "The date taken")
        position_id = self._repo.add(
            symbol.strip().upper(),
            key,
            quantity,
            entry,
            stop,
            opened_on,
            note,
            planned=planned and stop is not None,
        )
        self._changed()
        position = self._repo.get(position_id)
        assert position is not None
        return position

    def update_position(
        self,
        position_id: int,
        *,
        quantity: int,
        entry: float,
        stop: float | None,
        note: str = "",
        opened_on: dt.date | None = None,
    ) -> None:
        """
        A stop at or above entry is fine here - that's a trailed stop. On an
        imported position only the stop and note can change: the broker owns
        the quantity, price and dates.
        """
        _check_position(quantity, entry, stop)
        position = self._repo.get(position_id)
        if position is None:
            raise RiskInputError("That position no longer exists.")
        if position.is_imported and (
            quantity != position.quantity
            or abs(entry - position.entry) > 0.005
            or (opened_on is not None and opened_on != position.opened_on)
        ):
            raise RiskInputError(
                f"Quantity, price and dates come from {position.source.title()} - "
                "only the stop and note can be edited."
            )
        opened_on = opened_on or position.opened_on
        self._check_not_future(opened_on, "The date taken")
        if position.closed_on is not None and opened_on > position.closed_on:
            raise RiskInputError("The date taken can't be after the exit date.")
        self._repo.update(
            position_id, quantity=quantity, entry=entry, stop=stop, note=note, opened_on=opened_on
        )
        self._changed()

    def close_position(
        self, position_id: int, exit_price: float, closed_on: dt.date | None = None
    ) -> None:
        """`closed_on`: the day the trade was exited (default today)."""
        if exit_price <= 0:
            raise RiskInputError("The exit price must be above zero.")
        position = self._repo.get(position_id)
        if position is None:
            raise RiskInputError("That position no longer exists.")
        if position.is_imported:
            raise RiskInputError(
                f"Exits come from {position.source.title()} - sell there, then sync."
            )
        closed_on = closed_on or self._calendar.today()
        self._check_not_future(closed_on, "The exit date")
        if closed_on < position.opened_on:
            raise RiskInputError(
                f"The exit date can't be before the date taken ({position.opened_on:%d %b %Y})."
            )
        self._repo.close(position_id, exit_price, closed_on)
        self._changed()

    def delete_position(self, position_id: int) -> None:
        """An imported position is also remembered as ignored, so syncs don't bring it back."""
        position = self._repo.get(position_id)
        if position is not None and position.is_imported and position.broker_ref:
            self._repo.ignore(position.source, position.broker_ref)
        self._repo.delete(position_id)
        self._changed()

    # --- lifecycle ---------------------------------------------------------

    def events(self) -> list[str]:
        drained = list(self._events)
        self._events.clear()
        return drained

    def close(self) -> None:
        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None

    # --- internals ---------------------------------------------------------

    def _changed(self) -> None:
        self._positions = None
        self.version += 1
        self._resubscribe()

    def _check_not_future(self, day: dt.date, what: str) -> None:
        if day > self._calendar.today():
            raise RiskInputError(f"{what} can't be in the future.")

    def _key(self, symbol: str | None) -> str | None:
        if not symbol or not symbol.strip():
            return None
        try:
            return self._instruments.find_instrument_key(symbol)
        except InstrumentsUnavailableError:
            return None

    def _resubscribe(self) -> None:
        keys = {p.instrument_key for p in self.positions() if p.is_open and p.instrument_key}
        if self._watched_key:
            keys.add(self._watched_key)
        wanted = frozenset(keys)
        if wanted == self._subscribed:
            return
        for key in wanted - self._subscribed:
            self._ensure_history(key)
        try:
            if not wanted:
                if self._subscription is not None:
                    self._subscription.close()
                    self._subscription = None
            elif self._subscription is None:
                self._subscription = self._hub.subscribe(sorted(wanted), self._on_tick)
            else:
                self._subscription.update(sorted(wanted))
        except Exception as exc:  # e.g. the feed's instrument cap
            logger.warning("risk price subscription failed", exc_info=True)
            self._events.append(f"live prices unavailable: {exc}")
            return
        self._subscribed = wanted

    def _on_tick(
        self, key: str, vtt: int | None, ltp: float | None, prev_close: float | None
    ) -> None:
        if ltp is not None and ltp > 0:
            self._ticks[key] = (ltp, time.monotonic())

    def _quote(self, key: str, bars: tuple[DailyBar, ...]) -> Quote | None:
        """The newest of a feed tick and an API quote; else the last cached close."""
        tick, rest = self._ticks.get(key), self._rest.get(key)
        if tick is not None and (rest is None or tick[1] >= rest[1]):
            return Quote(tick[0], "live")
        if rest is not None:
            return Quote(rest[0], "quote", rest[2])
        if bars:
            return Quote(bars[-1].close, "close", bars[-1].date)
        return None

    def _refresh_quotes(self, keys: Sequence[str]) -> None:
        """Ask the quote API (on a worker) for keys the feed hasn't updated lately."""
        now = time.monotonic()
        stale = [
            key
            for key in dict.fromkeys(keys)
            if now - self._ticks.get(key, (0.0, float("-inf")))[1] >= QUOTE_REFRESH_SECONDS
            and now - self._quote_attempts.get(key, float("-inf")) >= QUOTE_REFRESH_SECONDS
        ]
        with self._lock:
            if not stale or self._quotes_in_flight:
                return
            self._quotes_in_flight = True
            for key in stale:
                self._quote_attempts[key] = now
        threading.Thread(
            target=self._fetch_quotes, args=(stale,), name="risk-quotes", daemon=True
        ).start()

    def _fetch_quotes(self, keys: list[str]) -> None:
        try:
            prices = self._quotes.ltp(keys)
        except Exception as exc:
            logger.warning("risk quote fetch failed", exc_info=True)
            message = f"quotes unavailable: {exc}"
            if message != self._quote_error:  # say it once, not every minute
                self._events.append(message)
            self._quote_error = message
        else:
            self._quote_error = None
            stamp = time.monotonic()
            label = self._calendar.now().strftime("%H:%M")
            for key, price in prices.items():
                if price > 0:
                    self._rest[key] = (price, stamp, label)
        finally:
            with self._lock:
                self._quotes_in_flight = False

    def _lookback_days(self) -> int:
        settings = self.settings
        sessions = max(settings.atr_period + 1, settings.low_sessions, _VOLUME_SESSIONS + 1)
        return int(sessions * 1.6) + 15  # calendar days: weekends and holidays

    def _history(self, key: str) -> tuple[DailyBar, ...]:
        today = self._calendar.today()
        if today != self._bars_day:
            self._bars, self._bars_day = {}, today
        bars = self._bars.get(key)
        if bars is None:
            bars = tuple(self._candles.cached(key, self._lookback_days()))
            self._bars[key] = bars
        return bars

    def _ensure_history(self, key: str) -> None:
        today = self._calendar.today()
        with self._lock:
            if key in self._fetching or (key, today) in self._attempted:
                return
            self._attempted.add((key, today))
        if self._candles.is_current(key):
            return
        with self._lock:
            self._fetching.add(key)
        threading.Thread(
            target=self._fetch_history, args=(key,), name="risk-history", daemon=True
        ).start()

    def _fetch_history(self, key: str) -> None:
        try:
            self._candles.daily(key, self._lookback_days())
        except Exception as exc:
            logger.warning("risk history fetch failed for %s", key, exc_info=True)
            self._events.append(f"history for {key.partition('|')[2] or key} failed: {exc}")
        finally:
            with self._lock:
                self._fetching.discard(key)
            self._bars.pop(key, None)
            self.version += 1

    def _flags_for(self, symbol: str) -> tuple[PriceBand | None, Surveillance]:
        snapshot = self._securities.snapshot()
        if snapshot.version != self._flags_version:
            flags: dict[str, tuple[PriceBand | None, Surveillance]] = {
                e.symbol: (e.band, NOT_UNDER_SURVEILLANCE) for e in snapshot.etfs
            }
            flags.update({e.symbol: (e.band, e.surveillance) for e in snapshot.equities})
            self._flags, self._flags_version = flags, snapshot.version
        return self._flags.get(symbol, (None, NOT_UNDER_SURVEILLANCE))


def _check_position(quantity: int, entry: float, stop: float | None) -> None:
    if quantity <= 0:
        raise RiskInputError("Quantity must be at least 1.")
    if entry <= 0 or (stop is not None and stop <= 0):
        raise RiskInputError("Entry and stop must be above zero.")
