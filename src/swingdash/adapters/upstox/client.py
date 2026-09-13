"""
Builds Upstox SDK API objects. All authentication goes through here, so if
auth ever changes (e.g. a second, order-placing OAuth token alongside the
read-only Analytics Token) it changes in one place.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from multiprocessing.pool import ThreadPool
from typing import Any

import upstox_client
import upstox_client.api_client as _sdk_api_client


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


class UpstoxClient:
    """
    `token` is a callable so the token is only demanded when an API call is
    actually made - commands that never talk to Upstox work without one.

    Every accessor builds a fresh ApiClient on purpose: API objects are used
    from worker threads (baseline builds), and a shared ApiClient's
    connection state isn't documented as thread-safe.
    """

    def __init__(self, token: Callable[[], str]) -> None:
        self._token = token

    def api_client(self) -> upstox_client.ApiClient:
        configuration = upstox_client.Configuration()
        configuration.access_token = self._token()
        return upstox_client.ApiClient(configuration)

    def market_quote_v3(self) -> upstox_client.MarketQuoteV3Api:
        return upstox_client.MarketQuoteV3Api(self.api_client())

    def history_v3(self) -> upstox_client.HistoryV3Api:
        return upstox_client.HistoryV3Api(self.api_client())

    def fundamentals(self) -> upstox_client.FundamentalsApi:
        return upstox_client.FundamentalsApi(self.api_client())

    def market_calendar(self) -> upstox_client.MarketHolidaysAndTimingsApi:
        return upstox_client.MarketHolidaysAndTimingsApi(self.api_client())
