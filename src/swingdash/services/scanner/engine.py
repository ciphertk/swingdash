"""
ScannerEngine - live Burst Power and Mswing for a watchlist.

Work is split so a redraw costs almost nothing (see domain/scanner.py):

- History (background, DailyContextLoader): cached daily candles are
  prepared at once, so the table fills in about a second; only symbols whose
  cache is behind are fetched, and re-prepared as they land.
- Ticks (feed thread): two scalar writes, exactly like RvolEngine.
- snapshot() (UI timer): an O(1) live step per symbol.

Prices come through the shared MarketDataHub, so symbols also open in Live
RVOL cost no extra feed traffic.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from collections.abc import Callable

from swingdash.domain.calendar import Session
from swingdash.domain.scanner import (
    ScannerIndex,
    ScannerRow,
    ScannerSnapshot,
    SymbolContext,
    SymbolMetrics,
    TodayBar,
    live_symbol,
    mswing_class,
)
from swingdash.services.candles import CandleService
from swingdash.services.daily_contexts import ContextJob, DailyContextLoader, session_before
from swingdash.services.market_data_hub import MarketDataHub, Subscription
from swingdash.services.ports import MarketCalendar

logger = logging.getLogger(__name__)

# How often the active session is re-checked, to roll over at the open.
SESSION_CHECK_SECONDS = 30.0


class _State:
    """One instrument. Written lock-free: attribute rebinding is atomic under CPython."""

    __slots__ = ("context", "instrument_key", "ltp", "symbol")

    def __init__(self, symbol: str, instrument_key: str) -> None:
        self.symbol = symbol
        self.instrument_key = instrument_key
        self.context: SymbolContext | None = None
        self.ltp: float | None = None


class ScannerEngine:
    def __init__(
        self,
        symbols: list[str],
        *,
        calendar: MarketCalendar,
        candles: CandleService,
        resolve_key: Callable[[str], str | None],
        hub: MarketDataHub,
        index_key: str,
        index_name: str,
    ) -> None:
        self._calendar = calendar
        self._loader = DailyContextLoader(candles, name="scanner")
        self._resolve_key = resolve_key
        self._hub = hub
        self._subscription: Subscription | None = None
        self._events: deque[str] = deque(maxlen=200)
        self._market_status = "UNKNOWN"
        self._stop = threading.Event()

        self._session: Session | None = None
        self._previous_session: dt.date | None = None
        self._index = _State(index_name, index_key)
        self._states, self._unresolved = self._resolve(symbols, {})

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        for symbol in self._unresolved:
            self._events.append(f"unknown symbol: {symbol}")
        self._subscription = self._hub.subscribe(self._feed_keys(), self._on_tick, self._on_status)
        self._loader.start()
        threading.Thread(target=self._run, name="scanner", daemon=True).start()

    def stop(self) -> None:
        """Releases this engine's instruments; the shared feed stays up."""
        self._stop.set()
        self._loader.stop()
        if self._subscription is not None:
            self._subscription.close()

    def set_symbols(self, symbols: list[str]) -> None:
        """Switch watchlists: keeps what stays, loads only what arrived. See RvolEngine."""
        previous = self._states
        states, unresolved = self._resolve(symbols, previous)
        self._states, self._unresolved = states, unresolved
        if self._subscription is not None:
            self._subscription.update(self._feed_keys())
        for symbol in unresolved:
            self._events.append(f"unknown symbol: {symbol}")
        added = [state for key, state in states.items() if key not in previous]
        if added and self._session is not None:
            date = self._session.date
            self._loader.load_async([self._job(s, date) for s in added], date)

    def set_index(self, instrument_key: str, name: str) -> None:
        self._index = _State(name, instrument_key)
        if self._subscription is not None:
            self._subscription.update(self._feed_keys())
        if self._session is not None:
            date = self._session.date
            self._loader.load_async([self._job(self._index, date)], date)

    @property
    def index_key(self) -> str:
        return self._index.instrument_key

    # --- history -------------------------------------------------------------

    def _run(self) -> None:
        """Load for the current session, then roll over whenever a new one opens."""
        while not self._stop.is_set():
            try:
                session = self._calendar.active_session()
                if session is not None and (
                    self._session is None or session.date != self._session.date
                ):
                    if self._session is not None:
                        self._events.append(f"session {self._session.date} -> {session.date}")
                    self._previous_session = session_before(self._calendar, session.date)
                    self._session = session
                    states = [self._index, *self._states.values()]
                    self._loader.load([self._job(s, session.date) for s in states], session.date)
            except Exception:
                logger.exception("scanner session check failed")
            self._stop.wait(SESSION_CHECK_SECONDS)

    def _job(self, state: _State, session_date: dt.date) -> ContextJob:
        def deliver(context: SymbolContext | None) -> None:
            state.context = context

        def wanted() -> bool:
            # Not dropped from the watchlist, not replaced as the index, and no
            # session rollover since - old-session work must not land.
            live = state is self._index or self._states.get(state.instrument_key) is state
            return live and self._session is not None and self._session.date == session_date

        return ContextJob(state.instrument_key, state.symbol, deliver, wanted)

    # --- feed callbacks (feed thread) --------------------------------------

    def _on_tick(
        self, key: str, vtt: int | None, ltp: float | None, prev_close: float | None
    ) -> None:
        if ltp is None:
            return
        state = self._states.get(key)
        if state is not None:
            state.ltp = ltp
        if key == self._index.instrument_key:
            self._index.ltp = ltp

    def _on_status(self, status: str) -> None:
        self._market_status = status

    # --- UI-facing ---------------------------------------------------------

    def snapshot(self) -> ScannerSnapshot:
        session = self._session
        closed = session is not None and self._calendar.now() >= session.close_at

        def metrics(state: _State) -> SymbolMetrics | None:
            if state.context is None or session is None:
                return None
            today = TodayBar(session.date, self._previous_session, state.ltp, closed)
            return live_symbol(state.context, today)

        index_state = self._index
        index_metrics = metrics(index_state)
        rows: list[ScannerRow] = []
        for state in self._states.values():
            stock = metrics(state)
            vs_index = None
            klass = None
            if stock is not None and index_metrics is not None:
                klass = mswing_class(stock.mswing, index_metrics.mswing)
                if stock.mswing.score is not None and index_metrics.mswing.score is not None:
                    vs_index = stock.mswing.score - index_metrics.mswing.score
            context = state.context
            ltp = state.ltp
            if ltp is None and context is not None:
                ltp = context.last_close  # no tick yet: the last completed close
            rows.append(
                ScannerRow(
                    symbol=state.symbol,
                    instrument_key=state.instrument_key,
                    ltp=ltp,
                    change_pct=stock.change_pct if stock else None,
                    metrics=stock,
                    history_through=context.history_through if context else None,
                    mswing_class=klass,
                    vs_index=vs_index,
                )
            )

        loaded = sum(1 for state in self._states.values() if state.context is not None)
        return ScannerSnapshot(
            rows=tuple(rows),
            index=ScannerIndex(index_state.instrument_key, index_state.symbol, index_metrics),
            session_date=session.date if session else None,
            session_closed=closed,
            loaded=loaded,
            total=len(self._states),
            fetching=self._loader.pending > 0,
            market_status=self._market_status,
        )

    def events(self) -> list[str]:
        out = []
        while self._events:
            out.append(self._events.popleft())
        return out + self._loader.events()

    # --- helpers -------------------------------------------------------------

    def _resolve(
        self, symbols: list[str], existing: dict[str, _State]
    ) -> tuple[dict[str, _State], list[str]]:
        states: dict[str, _State] = {}
        unresolved: list[str] = []
        for symbol in symbols:
            key = self._resolve_key(symbol)
            if key is None:
                unresolved.append(symbol)
            else:
                states[key] = existing.get(key) or _State(symbol, key)
        return states, unresolved

    def _feed_keys(self) -> list[str]:
        return [*self._states, self._index.instrument_key]
