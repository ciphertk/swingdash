"""
RvolEngine - owns live state, serves snapshots.

Shape of the thing: after startup there are NO network calls per update.
A tick writes three scalars; all division happens in snapshot(), which a
UI polls ~10x/sec. Ticks arrive far faster than any screen needs
repainting, so computing per tick would be mostly wasted work.

Deliberately asyncio-free and UI-free: the TUI polls snapshot() on a
timer and drains events() the same way. That keeps one engine usable by
the TUI and any second UI - which matters, because Upstox allows
only 2 feed connections per account, so the UIs must share one engine
process rather than each opening a feed.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from swingdash.live import baseline as baseline_store
from swingdash.live import rvol_calc
from swingdash.live.feed import Feed
from swingdash.live.session import Session, active_session, now_ist
from swingdash.live.types import Baseline, Snapshot, SymbolRow
from swingdash.services.instrument_service import find_instrument_key

# Each baseline is one ~1.3s call (a month of 1-minute candles is a big
# payload), so cold-start time is essentially 150/workers * 1.3s. 16 keeps
# the observed rate near 12 calls/sec - well inside Upstox's 50/sec, and a
# whole watchlist is far below the 500/min budget.
MAX_BASELINE_WORKERS = 16

# In-session silence this long means the symbol genuinely isn't trading;
# that's information, so the row is marked rather than hidden.
STALE_AFTER_SECONDS = 120

FLASH_SECONDS = 1.5


class SymbolState:
    """
    One per symbol, created before the feed starts and never replaced, so
    the state dict is only ever read - never structurally mutated - while
    the SDK thread is writing into it.
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
    def __init__(self, symbols: list[str], alert_ratio: float = rvol_calc.STRONG_RATIO) -> None:
        self._alert_ratio = alert_ratio
        self._events: deque[str] = deque(maxlen=200)
        self._ticks = 0
        self._market_status = "UNKNOWN"
        self._minute: int | None = None
        self._stop = threading.Event()

        # The last session that has opened, not today's: on a weekend,
        # holiday or before 09:15 there is no live session today, and the
        # volume on screen belongs to the previous one.
        self._session: Session | None = active_session()
        self._session_date = self._session.date if self._session else now_ist().date()

        self._states, self._unresolved = self._resolve(symbols, {})

        self._feed = Feed(
            list(self._states),
            on_tick=self._on_tick,
            on_status=self._on_status,
            on_connection=lambda state: self._events.append(f"feed {state}"),
        )

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """
        Returns as soon as the feed is connecting. Baselines finish filling
        in on background threads, so rows appear (and become live) as each
        one lands instead of blocking the whole table on the slowest fetch.
        """
        for symbol in self._unresolved:
            self._events.append(f"unknown symbol: {symbol}")

        cached = baseline_store.load_many(list(self._states), self._session_date)
        for key, baseline in cached.items():
            self._states[key].baseline = baseline

        self._feed.start()
        threading.Thread(target=self._minute_ticker, daemon=True).start()

        missing = [k for k, s in self._states.items() if s.baseline is None]
        if missing:
            self._events.append(f"building {len(missing)} baselines...")
            threading.Thread(target=self._build_missing, args=(missing,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._feed.stop()

    @staticmethod
    def _resolve(
        symbols: list[str], existing: dict[str, SymbolState]
    ) -> tuple[dict[str, SymbolState], list[str]]:
        """Reuses the SymbolState of any symbol that's staying, so its live
        volume and baseline survive a watchlist switch."""
        states: dict[str, SymbolState] = {}
        unresolved: list[str] = []
        for symbol in symbols:
            key = find_instrument_key(symbol)
            if key is None:
                unresolved.append(symbol)
            else:
                states[key] = existing.get(key) or SymbolState(symbol, key)
        return states, unresolved

    def set_symbols(self, symbols: list[str]) -> None:
        """
        Switch watchlists on the live connection - unsubscribing what left
        and subscribing what arrived, rather than reconnecting.

        The new state dict is built separately and then rebound in one
        assignment. Rebinding is atomic under CPython, so the feed thread
        always sees either the old dict or the new one, never a
        half-mutated one - which is why no lock is needed even though
        snapshot() iterates it.
        """
        previous = self._states
        states, unresolved = self._resolve(symbols, previous)

        self._states = states
        self._unresolved = unresolved

        added = set(states) - set(previous)
        removed = set(previous) - set(states)
        self._feed.unsubscribe(list(removed))
        self._feed.subscribe(list(added))

        for symbol in unresolved:
            self._events.append(f"unknown symbol: {symbol}")

        if not added:
            return

        cached = baseline_store.load_many(list(added), self._session_date)
        for key, baseline in cached.items():
            states[key].baseline = baseline

        missing = [k for k in added if states[k].baseline is None]
        if missing:
            self._events.append(f"building {len(missing)} baselines...")
            threading.Thread(target=self._build_missing, args=(missing,), daemon=True).start()

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
                return  # dropped from the watchlist while we were fetching
            try:
                built = baseline_store.build_baseline(key, session)
            except Exception as exc:
                self._events.append(f"{state.symbol}: baseline failed ({type(exc).__name__})")
                return
            if built is None:
                self._events.append(f"{state.symbol}: no history")
                return
            # Each worker thread gets its own SQLite connection via
            # app.db.get_connection (thread-local) - a shared connection
            # here previously caused sqlite3.InterfaceError under load.
            baseline_store.save(key, session.date, built)
            # A session rollover may have happened while this was fetching;
            # a baseline for the old session must not land on the new one.
            if self._session is not None and self._session.date == session.date:
                state.baseline = built

        with ThreadPoolExecutor(max_workers=MAX_BASELINE_WORKERS) as pool:
            list(pool.map(build, keys))
        self._events.append("baselines ready")

    def _minute_ticker(self) -> None:
        """
        Minute-of-session is derived once a second here rather than per
        tick - it changes 375 times a day, while ticks arrive thousands of
        times a minute.

        The active session is re-checked once per wall-clock minute. Sessions
        open on a minute boundary, so this catches e.g. Monday's 09:15 open
        within about a second without looking it up every second.
        """
        checked_minute = None
        while not self._stop.is_set():
            now = now_ist()
            this_minute = now.replace(second=0, microsecond=0)
            if this_minute != checked_minute:
                checked_minute = this_minute
                latest = active_session(now)
                if latest is not None and (
                    self._session is None or latest.date != self._session.date
                ):
                    self._roll_session(latest)

            # minute_of clamps past the close, so a finished session - e.g.
            # Friday's, viewed on Sunday - reads at its final minute, where
            # intraday RVOL and day RVOL converge.
            self._minute = self._session.minute_of(now) if self._session else None
            self._stop.wait(1.0)

    def _roll_session(self, session: Session) -> None:
        """
        Move every symbol onto a newly opened session. Baselines are
        per-session, so they're swapped for the new date's; volume is
        cleared because until the first tick of the new session it still
        holds the previous session's full-day total, which divided by the
        new session's minute-0 baseline would read as an enormous spike.
        """
        rolling_from = self._session.date if self._session else None
        self._session = session
        self._session_date = session.date
        if rolling_from is not None:
            self._events.append(f"session {rolling_from} -> {session.date}")

        states = self._states
        cached = baseline_store.load_many(list(states), session.date)
        for key, state in states.items():
            state.baseline = cached.get(key)
            state.vtt = None
            state.last_bucket = "unknown"

        missing = [k for k, s in states.items() if s.baseline is None]
        if missing:
            self._events.append(f"building {len(missing)} baselines...")
            threading.Thread(target=self._build_missing, args=(missing,), daemon=True).start()

    # --- feed callbacks (SDK thread) ---------------------------------------

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
            ratio = rvol_calc.rvol_intraday(state.vtt, state.baseline, minute)
            day_ratio = rvol_calc.rvol_day(state.vtt, state.baseline)
            bucket = rvol_calc.classify(ratio)

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
