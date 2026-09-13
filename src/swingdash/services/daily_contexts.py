"""
DailyContextLoader - prepared daily-history contexts (domain/scanner.py) for
any set of instruments, cheaply.

Shared by the Scanner and the Chartink tab: both need Burst Power (and
friends) for arbitrary symbols without refetching history they already have.

- Cache first: each instrument's cached daily candles are prepared at once,
  so numbers appear in about a second - even if a day stale.
- Then only instruments whose cache is behind are fetched (one small call
  each, once per trading day) on daemon workers, and re-prepared as they
  land. Daemon threads, so a worker waiting on the Upstox rate limiter never
  holds up quitting.
- A 429 re-queues the instrument: the Upstox client has already paused every
  caller.

The caller owns where a context goes: each job says how to deliver it and
whether it is still wanted (a watchlist switch, a session roll or a new
Chartink run can make old work moot).
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from swingdash.domain.calendar import MAX_LOOKBACK_DAYS
from swingdash.domain.errors import RateLimitedError
from swingdash.domain.scanner import SymbolContext, prepare_symbol
from swingdash.services.candles import CandleService
from swingdash.services.ports import MarketCalendar

logger = logging.getLogger(__name__)

# Burst Power looks back 3 years from today and needs the bar before that
# cutoff; a few extra days cover weekends and holidays at the boundary.
HISTORY_DAYS = 3 * 366 + 10

# Parallel history fetches. The Upstox adapter's shared rate limiter is what
# really paces them; this just bounds how many wait at once.
FETCH_WORKERS = 8

# Say "rate limited" at most this often, however many fetches it hits.
RATE_LIMIT_NOTICE_SECONDS = 60.0


def _always() -> bool:
    return True


@dataclass(frozen=True)
class ContextJob:
    instrument_key: str
    label: str  # e.g. the symbol, for messages
    deliver: Callable[[SymbolContext | None], None]
    wanted: Callable[[], bool] = field(default=_always)


class DailyContextLoader:
    def __init__(
        self, candles: CandleService, *, workers: int = FETCH_WORKERS, name: str = "history"
    ) -> None:
        self._candles = candles
        self._workers = workers
        self._name = name
        self._jobs: queue.Queue[tuple[ContextJob, dt.date]] = queue.Queue()
        self._stop = threading.Event()
        self._started = False
        self._start_lock = threading.Lock()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._rate_limited_at = float("-inf")
        self._events: deque[str] = deque(maxlen=200)

    @property
    def pending(self) -> int:
        """Fetches queued or in flight."""
        return self._pending

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
        for number in range(self._workers):
            threading.Thread(
                target=self._fetch_worker, name=f"{self._name}-fetch-{number}", daemon=True
            ).start()

    def stop(self) -> None:
        self._stop.set()

    def events(self) -> list[str]:
        out: list[str] = []
        while self._events:
            out.append(self._events.popleft())
        return out

    def load(self, jobs: Sequence[ContextJob], session_date: dt.date) -> None:
        """Blocking for the cache pass (call it off the UI thread); fetches continue in the background."""
        self.start()
        for job in jobs:
            if self._stop.is_set():
                return
            try:
                bars = self._candles.cached(job.instrument_key, HISTORY_DAYS)
                if bars and job.wanted():
                    job.deliver(prepare_symbol(bars, session_date))
            except Exception:
                logger.exception("cache read failed for %s", job.label)
        for job in jobs:
            try:
                current = self._candles.is_current(job.instrument_key)
            except Exception:
                current = False
            if not current:
                with self._pending_lock:
                    self._pending += 1
                self._jobs.put((job, session_date))

    def load_async(self, jobs: Sequence[ContextJob], session_date: dt.date) -> None:
        threading.Thread(
            target=self.load,
            args=(list(jobs), session_date),
            name=f"{self._name}-load",
            daemon=True,
        ).start()

    def _fetch_worker(self) -> None:
        while not self._stop.is_set():
            try:
                job, session_date = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            requeue = False
            try:
                if not self._stop.is_set() and job.wanted():
                    bars = self._candles.daily(job.instrument_key, HISTORY_DAYS)
                    if job.wanted():
                        job.deliver(prepare_symbol(bars, session_date) if bars else None)
            except RateLimitedError:
                requeue = True
                now = time.monotonic()
                if now - self._rate_limited_at > RATE_LIMIT_NOTICE_SECONDS:
                    self._rate_limited_at = now
                    self._events.append("Upstox rate limit - history paused, retrying")
            except Exception as exc:
                logger.warning("history fetch failed for %s", job.label, exc_info=True)
                self._events.append(f"{job.label}: history failed ({type(exc).__name__})")
            finally:
                if requeue and not self._stop.is_set():
                    self._jobs.put((job, session_date))
                else:
                    with self._pending_lock:
                        self._pending -= 1


def session_before(calendar: MarketCalendar, date: dt.date) -> dt.date | None:
    """The trading session before `date`, if one is within MAX_LOOKBACK_DAYS."""
    day = date
    for _ in range(MAX_LOOKBACK_DAYS):
        day -= dt.timedelta(days=1)
        if calendar.get_session(day) is not None:
            return day
    return None
