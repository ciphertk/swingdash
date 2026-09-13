"""
Builds Upstox SDK API objects. All authentication goes through here, so if
auth ever changes (e.g. a second, order-placing OAuth token alongside the
read-only Analytics Token) it changes in one place.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from multiprocessing.pool import ThreadPool
from typing import Any, TypeVar

import upstox_client
import upstox_client.api_client as _sdk_api_client
from upstox_client.rest import ApiException

from swingdash.domain.errors import RateLimitedError

T = TypeVar("T")


class LazyThreadPool:
    """
    Stand-in for the ThreadPool the Upstox SDK starts in EVERY ApiClient.

    The SDK only uses that pool for `async_req=True` calls, which swingdash
    never makes - yet each ApiClient spawned one thread per CPU (11 here),
    and swingdash builds a client per API call. Worse, the SDK tears the pool
    down in `ApiClient.__del__`; for a client still alive when Python shuts
    down, that runs after the pool's pipe handles are closed and prints
    "Error during ApiClient cleanup: [WinError 6] The handle is invalid".

    This starts the real pool only on first use, and close/join are no-ops
    if it never started - so there are no threads, and nothing to fail at
    exit.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs
        self._pool: ThreadPool | None = None
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._pool is not None

    def apply_async(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPool(*self._args, **self._kwargs)
        return self._pool.apply_async(*args, **kwargs)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def join(self) -> None:
        if self._pool is not None:
            self._pool.join()


# The SDK builds its pool as `ThreadPool()` looked up in its own module at
# construction time, so swapping the name here covers every ApiClient -
# including the feed's. Applied on import, before any client exists.
_sdk_api_client.ThreadPool = LazyThreadPool  # type: ignore[attr-defined]


class RateLimiter:
    """
    Blocks until one more call fits inside every (limit, seconds) window.

    Thread-safe: history fetches, baseline builds and the sector backfill
    all draw on the same Upstox quota from different worker threads.
    """

    def __init__(
        self,
        windows: Sequence[tuple[int, float]],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._windows = [(limit, seconds, deque[float]()) for limit, seconds in windows]
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._paused_until = float("-inf")

    def pause(self, seconds: float) -> None:
        """Hold every caller back for `seconds` - after the server says slow down."""
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + seconds)

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                wait = self._paused_until - now
                for limit, seconds, stamps in self._windows:
                    while stamps and stamps[0] <= now - seconds:
                        stamps.popleft()
                    if len(stamps) >= limit:
                        wait = max(wait, stamps[0] + seconds - now)
                if wait <= 0:
                    for _, _, stamps in self._windows:
                        stamps.append(now)
                    return
            self._sleep(wait)


# Upstox documents 50/second, 500/minute and 2000 per 30 minutes per account.
# Its Cloudflare edge is stricter per IP: bursts near 40/second on the
# history endpoint drew "Error 1015: You are being rate limited" (Sep 2026),
# while ~12/second (Live RVOL's baseline builds) has always been fine.
UPSTOX_LIMITS: tuple[tuple[int, float], ...] = ((10, 1.0), (250, 60.0), (1500, 1800.0))
_shared_limiter = RateLimiter(UPSTOX_LIMITS)

# Every REST call gets these (connect, read) seconds. The SDK's default is no
# timeout at all, so a request the edge holds open blocked its thread
# indefinitely. Rate-limited requests are held ~20s before the 429 arrives.
REQUEST_TIMEOUT = (10.0, 45.0)

# After a 429, every REST caller waits this long before trying again.
RATE_LIMIT_PAUSE_SECONDS = 60.0

_TOO_MANY_REQUESTS = 429


class UpstoxClient:
    """
    `token` is a callable so the token is only demanded when an API call is
    actually made - commands that never talk to Upstox work without one.

    Every REST accessor builds a fresh API object on purpose: they're used
    from worker threads (baseline builds), and a shared ApiClient's
    connection state isn't documented as thread-safe. Each one is built for
    exactly one call, so each takes one slot from the shared rate limiter.
    """

    def __init__(self, token: Callable[[], str], limiter: RateLimiter | None = None) -> None:
        self._token = token
        self._limiter = limiter or _shared_limiter

    def api_client(self) -> upstox_client.ApiClient:
        """Unmetered - also used by the feed, which must never wait behind REST calls."""
        configuration = upstox_client.Configuration()
        configuration.access_token = self._token()
        return upstox_client.ApiClient(configuration)

    def _metered(self) -> upstox_client.ApiClient:
        self._limiter.acquire()
        return self.api_client()

    def call(self, method: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Make one REST call through a metered API object: with a timeout, and
        on a 429 pause every caller before raising RateLimitedError.
        """
        try:
            return method(*args, _request_timeout=REQUEST_TIMEOUT, **kwargs)
        except ApiException as exc:
            if exc.status == _TOO_MANY_REQUESTS:
                self._limiter.pause(RATE_LIMIT_PAUSE_SECONDS)
                raise RateLimitedError("Upstox rate limit reached") from exc
            raise

    def market_quote_v3(self) -> upstox_client.MarketQuoteV3Api:
        return upstox_client.MarketQuoteV3Api(self._metered())

    def history_v3(self) -> upstox_client.HistoryV3Api:
        return upstox_client.HistoryV3Api(self._metered())

    def fundamentals(self) -> upstox_client.FundamentalsApi:
        return upstox_client.FundamentalsApi(self._metered())

    def market_calendar(self) -> upstox_client.MarketHolidaysAndTimingsApi:
        return upstox_client.MarketHolidaysAndTimingsApi(self._metered())
