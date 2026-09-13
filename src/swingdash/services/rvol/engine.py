"""
RvolEngine - owns live RVOL state and serves snapshots.

After startup there are NO network calls per update. A tick writes three
scalars; all division happens in snapshot(), which a UI polls ~8x/second.
Ticks arrive far faster than any screen repaints, so computing per tick
would be mostly wasted work.

Deliberately asyncio-free and UI-free: a UI polls snapshot() on a timer and
drains events() the same way.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from swingdash.domain.calendar import Session
from swingdash.domain.errors import RateLimitedError
from swingdash.domain.rvol import calc
from swingdash.domain.rvol.types import Baseline, Snapshot, SymbolRow
from swingdash.services.market_data_hub import MarketDataHub, Subscription
from swingdash.services.ports import MarketCalendar
from swingdash.services.rvol.baselines import BaselineService

logger = logging.getLogger(__name__)

# Each baseline is one ~1.3s call (a month of 1-minute candles is a large
# payload), so cold-start time is roughly symbols/workers * 1.3s. 16 keeps
# the observed rate near 12 calls/second - inside Upstox's 50/second limit.
MAX_BASELINE_WORKERS = 16

# In-session silence this long means the symbol genuinely isn't trading;
# that's information, so the row is marked rather than hidden.
STALE_AFTER_SECONDS = 120

FLASH_SECONDS = 1.5

# A baseline hit by Upstox's rate limit is retried this many times in all.
RATE_LIMIT_ATTEMPTS = 5


class SymbolState:
    """
    Mutable per-symbol state. The feed thread writes scalars into these
    objects without a lock (attribute assignment is atomic under CPython).
    """

    __slots__ = (
        "baseline",
        "flash_until",
        "instrument_key",
        "last_bucket",
        "last_tick_at",
        "ltp",
        "prev_close",
        "symbol",
        "vtt",
    )

    def __init__(self, symbol: str, instrument_key: str) -> None:
        self.symbol = symbol
        self.instrument_key = instrument_key
        self.baseline: Baseline | None = None
        self.vtt: int | None = None
        self.ltp: float | None = None
        self.prev_close: float | None = None
        self.last_tick_at: float = 0.0
        self.last_bucket: str = "unknown"
        self.flash_until: float = 0.0


class RvolEngine:
    def __init__(
        self,
        symbols: list[str],
        *,
        calendar: MarketCalendar,
        baselines: BaselineService,
        resolve_key: Callable[[str], str | None],
        hub: MarketDataHub,
        alert_ratio: float = calc.STRONG_RATIO,
    ) -> None:
        self._calendar = calendar
        self._hub = hub
        self._subscription: Subscription | None = None
        self._baselines = baselines
        self._resolve_key = resolve_key
        self._alert_ratio = alert_ratio
        self._events: deque[str] = deque(maxlen=200)
        self._ticks = 0
        self._market_status = "UNKNOWN"
        self._minute: int | None = None
        self._stop = threading.Event()

        # The last session that has opened, not today's: on a weekend,
        # holiday or before 09:15 there is no live session today, and the
        # volume on screen belongs to the previous one.
        self._session: Session | None = calendar.active_session()
        self._session_date = self._session.date if self._session else calendar.today()

        self._states, self._unresolved = self._resolve(symbols, {})

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """
        Returns as soon as the feed is connecting. Baselines fill in on
        background threads, so rows become live as each one lands instead of
        blocking the whole table on the slowest fetch.
        """
        for symbol in self._unresolved:
            self._events.append(f"unknown symbol: {symbol}")

        cached = self._baselines.load_many(list(self._states), self._session_date)
        for key, baseline in cached.items():
            self._states[key].baseline = baseline

        # Through the shared hub, never a feed of its own: Upstox allows only
        # two connections per account and other tabs need the same one.
        self._subscription = self._hub.subscribe(
            list(self._states),
            self._on_tick,
            self._on_status,
            lambda state: self._events.append(f"feed {state}"),
        )
        threading.Thread(target=self._minute_ticker, name="rvol-ticker", daemon=True).start()

        missing = [k for k, s in self._states.items() if s.baseline is None]
        if missing:
            self._start_baseline_build(missing)

    def stop(self) -> None:
        """Stops this engine and releases its instruments; the shared feed stays up."""
        self._stop.set()
        if self._subscription is not None:
            self._subscription.close()

    def _resolve(
        self, symbols: list[str], existing: dict[str, SymbolState]
    ) -> tuple[dict[str, SymbolState], list[str]]:
        """Reuses the state of symbols that stay, so their live volume and
        baseline survive a watchlist switch."""
        states: dict[str, SymbolState] = {}
        unresolved: list[str] = []
        for symbol in symbols:
            key = self._resolve_key(symbol)
            if key is None:
                unresolved.append(symbol)
            else:
                states[key] = existing.get(key) or SymbolState(symbol, key)
        return states, unresolved

    def set_symbols(self, symbols: list[str]) -> None:
        """
        Switch symbols on the live connection - the hub unsubscribes what left
        and subscribes what arrived, rather than reconnecting.

        The new state dict is built separately and rebound in one assignment.
        Rebinding is atomic under CPython, so the feed thread always sees the
        old dict or the new one, never a half-mutated one - which is why no
        lock is needed even though snapshot() iterates it.
        """
        previous = self._states
        states, unresolved = self._resolve(symbols, previous)

        self._states = states
        self._unresolved = unresolved

        added = set(states) - set(previous)
        if self._subscription is not None:
            self._subscription.update(list(states))

        for symbol in unresolved:
            self._events.append(f"unknown symbol: {symbol}")

        if not added:
            return

        cached = self._baselines.load_many(list(added), self._session_date)
        for key, baseline in cached.items():
            states[key].baseline = baseline

        missing = [k for k in added if states[k].baseline is None]
        if missing:
            self._start_baseline_build(missing)

    def _start_baseline_build(self, keys: list[str]) -> None:
        self._events.append(f"building {len(keys)} baselines...")
        threading.Thread(
            target=self._build_missing, args=(keys,), name="rvol-baselines", daemon=True
        ).start()

    def _build_missing(self, keys: list[str]) -> None:
        session = self._session
        if session is None:
            self._events.append("no recent trading session found - baselines unavailable")
            return

        def build(key: str) -> None:
            # Hold the state object rather than re-reading the dict: a
            # watchlist switch may rebind it mid-build, and writing through
            # the object still reaches whoever is holding it.
            state = self._states.get(key)
            if state is None:
                return  # dropped from the watchlist while fetching
            try:
                built = None
                for attempt in range(RATE_LIMIT_ATTEMPTS):
                    try:
                        built = self._baselines.build(key, session)
                        break
                    except RateLimitedError:
                        # The Upstox client already paused every caller; retry after.
                        if attempt == RATE_LIMIT_ATTEMPTS - 1 or self._stop.is_set():
                            raise
            except Exception as exc:
                logger.exception("baseline build failed for %s", state.symbol)
                self._events.append(f"{state.symbol}: baseline failed ({type(exc).__name__})")
                return
            if built is None:
                self._events.append(f"{state.symbol}: no history")
                return
            self._baselines.save(key, session.date, built)
            # A session rollover may have happened while this was fetching;
            # a baseline for the old session must not land on the new one.
            if self._session is not None and self._session.date == session.date:
                state.baseline = built

        with ThreadPoolExecutor(max_workers=MAX_BASELINE_WORKERS) as pool:
            list(pool.map(build, keys))
        self._events.append("baselines ready")

    def _minute_ticker(self) -> None:
        """
        Minute-of-session is derived once a second rather than per tick.

        The active session is re-checked once per wall-clock minute. Sessions
        open on a minute boundary, so this catches the 09:15 open within about
        a second without looking it up every second.
        """
        checked_minute = None
        while not self._stop.is_set():
            now = self._calendar.now()
            this_minute = now.replace(second=0, microsecond=0)
            if this_minute != checked_minute:
                checked_minute = this_minute
                latest = self._calendar.active_session(now)
                if latest is not None and (
                    self._session is None or latest.date != self._session.date
                ):
                    self._roll_session(latest)

            # minute_of clamps past the close, so a finished session reads at
            # its final minute, where intraday RVOL and day RVOL converge.
            self._minute = self._session.minute_of(now) if self._session else None
            self._stop.wait(1.0)

    def _roll_session(self, session: Session) -> None:
        """
        Move every symbol onto a newly opened session. Baselines are
        per-session, so they're swapped for the new date's; volume is
        cleared because until the first tick of the new session it still
        holds the previous session's total, which divided by the new
        session's minute-0 baseline would read as an enormous spike.
        """
        rolling_from = self._session.date if self._session else None
        self._session = session
        self._session_date = session.date
        if rolling_from is not None:
            self._events.append(f"session {rolling_from} -> {session.date}")

        states = self._states
        cached = self._baselines.load_many(list(states), session.date)
        for key, state in states.items():
            state.baseline = cached.get(key)
            state.vtt = None
            state.last_bucket = "unknown"

        missing = [k for k, s in states.items() if s.baseline is None]
        if missing:
            self._start_baseline_build(missing)

    # --- feed callbacks (feed thread) --------------------------------------

    def _on_tick(
        self, key: str, vtt: int | None, ltp: float | None, prev_close: float | None
    ) -> None:
        state = self._states.get(key)
        if state is None:
            return
        if vtt is not None:
            state.vtt = vtt
        if ltp is not None:
            state.ltp = ltp
        if prev_close is not None:
            state.prev_close = prev_close
        state.last_tick_at = time.monotonic()
        self._ticks += 1

    def _on_status(self, status: str) -> None:
        if status != self._market_status:
            self._events.append(f"NSE_EQ {status}")
        self._market_status = status

    # --- UI-facing ---------------------------------------------------------

    def snapshot(self) -> Snapshot:
        now = time.monotonic()
        minute = self._minute
        rows = []

        for state in self._states.values():
            ratio = calc.rvol_intraday(state.vtt, state.baseline, minute)
            day_ratio = calc.rvol_day(state.vtt, state.baseline)
            bucket = calc.classify(ratio)

            # Crossing into a hotter bucket is the alert - detected here so
            # the tick path stays a plain assignment.
            if bucket != state.last_bucket:
                if (
                    ratio is not None
                    and ratio >= self._alert_ratio
                    and state.last_bucket != "unknown"
                ):
                    state.flash_until = now + FLASH_SECONDS
                    self._events.append(f"{state.symbol} RVOL {ratio:.2f}x")
                state.last_bucket = bucket

            change_pct = None
            if state.ltp is not None and state.prev_close:
                change_pct = (state.ltp - state.prev_close) / state.prev_close * 100

            rows.append(
                SymbolRow(
                    symbol=state.symbol,
                    instrument_key=state.instrument_key,
                    ltp=state.ltp,
                    prev_close=state.prev_close,
                    change_pct=change_pct,
                    volume=state.vtt,
                    rvol=ratio,
                    rvol_day=day_ratio,
                    classification=bucket,
                    degraded=state.baseline.degraded if state.baseline else True,
                    stale=(
                        minute is not None
                        and state.last_tick_at > 0
                        and now - state.last_tick_at > STALE_AFTER_SECONDS
                    ),
                    flashing=now < state.flash_until,
                )
            )

        return Snapshot(
            rows=tuple(rows),
            minute=minute,
            session_date=self._session.date if self._session else None,
            session_minutes=self._session.minutes if self._session else 0,
            market_status=self._market_status,
            ticks_received=self._ticks,
        )

    def events(self) -> list[str]:
        """Drain pending events. Retained until read, so none are missed."""
        out = []
        while self._events:
            out.append(self._events.popleft())
        return out
