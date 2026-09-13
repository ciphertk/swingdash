"""
MarketDataHub - the app's single live market data connection.

Upstox allows only 2 WebSocket feed connections per account, so every
consumer (each dashboard tab, each live metric) subscribes through this hub
instead of opening its own feed.

Threading:
- Control plane (subscribe / update / close, called from UI and worker
  threads) runs under a lock. Per-instrument reference counts mean only
  0->1 and 1->0 transitions reach the socket.
- Data plane (ticks, on the feed's own thread) takes NO lock: routing tables
  are rebuilt on every change and rebound in a single assignment, which is
  atomic under CPython, so the feed thread always reads a complete table.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from swingdash.services.ports import FeedFactory, FeedTransport, OnConnection, OnStatus, OnTick

logger = logging.getLogger(__name__)

# Upstox's `full` mode subscription limit per connection.
MAX_INSTRUMENTS = 2000


class TooManyInstrumentsError(ValueError):
    pass


@dataclass(frozen=True)
class _Subscriber:
    keys: frozenset[str]
    on_tick: OnTick
    on_status: OnStatus | None
    on_connection: OnConnection | None


class Subscription:
    """A consumer's live interest in a set of instruments."""

    def __init__(self, hub: MarketDataHub, subscriber_id: int) -> None:
        self._hub = hub
        self._id = subscriber_id
        self._closed = False

    def update(self, instrument_keys: Sequence[str]) -> None:
        if not self._closed:
            self._hub._update(self._id, instrument_keys)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._hub._close(self._id)


class MarketDataHub:
    def __init__(self, feed_factory: FeedFactory) -> None:
        self._feed_factory = feed_factory
        self._feed: FeedTransport | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._subscribers: dict[int, _Subscriber] = {}
        self._refcounts: dict[str, int] = {}
        # Read lock-free by the feed thread; only ever replaced, never mutated.
        self._routes: dict[str, tuple[OnTick, ...]] = {}
        self._status_listeners: tuple[OnStatus, ...] = ()
        self._connection_listeners: tuple[OnConnection, ...] = ()
        self._broken_callbacks: set[int] = set()

        self.market_status = "UNKNOWN"
        self.connection_state = "idle"

    # --- control plane -----------------------------------------------------

    def subscribe(
        self,
        instrument_keys: Sequence[str],
        on_tick: OnTick,
        on_status: OnStatus | None = None,
        on_connection: OnConnection | None = None,
    ) -> Subscription:
        keys = frozenset(instrument_keys)
        with self._lock:
            self._check_capacity(extra=keys)
            subscriber_id = self._next_id
            self._next_id += 1
            self._subscribers[subscriber_id] = _Subscriber(keys, on_tick, on_status, on_connection)
            added = self._retain(keys)
            self._rebuild_routes()

            if self._feed is None:
                # First consumer: the connection starts with the full desired set.
                self._feed = self._feed_factory(self._on_tick, self._on_status, self._on_connection)
                self.connection_state = "connecting"
                self._feed.start(sorted(self._refcounts))
            elif added:
                self._feed.subscribe(sorted(added))
        return Subscription(self, subscriber_id)

    def _update(self, subscriber_id: int, instrument_keys: Sequence[str]) -> None:
        keys = frozenset(instrument_keys)
        with self._lock:
            current = self._subscribers.get(subscriber_id)
            if current is None:
                return
            self._check_capacity(extra=keys - current.keys, released=current.keys - keys)
            added = self._retain(keys - current.keys)
            removed = self._release(current.keys - keys)
            self._subscribers[subscriber_id] = _Subscriber(
                keys, current.on_tick, current.on_status, current.on_connection
            )
            self._rebuild_routes()
            if self._feed is not None:
                if removed:
                    self._feed.unsubscribe(sorted(removed))
                if added:
                    self._feed.subscribe(sorted(added))

    def _close(self, subscriber_id: int) -> None:
        with self._lock:
            current = self._subscribers.pop(subscriber_id, None)
            if current is None:
                return
            removed = self._release(current.keys)
            self._rebuild_routes()
            # The socket stays open even with no subscribers: reconnecting is
            # slower than idling, and the next tab will likely want it.
            if self._feed is not None and removed:
                self._feed.unsubscribe(sorted(removed))

    def close(self) -> None:
        with self._lock:
            feed, self._feed = self._feed, None
            self._subscribers.clear()
            self._refcounts.clear()
            self._rebuild_routes()
        if feed is not None:
            feed.stop()
        self.connection_state = "closed"

    @property
    def instrument_count(self) -> int:
        return len(self._refcounts)

    # --- helpers (lock held) -----------------------------------------------

    def _retain(self, keys: frozenset[str]) -> set[str]:
        newly_needed = set()
        for key in keys:
            count = self._refcounts.get(key, 0)
            if count == 0:
                newly_needed.add(key)
            self._refcounts[key] = count + 1
        return newly_needed

    def _release(self, keys: frozenset[str]) -> set[str]:
        no_longer_needed = set()
        for key in keys:
            count = self._refcounts.get(key, 0) - 1
            if count <= 0:
                self._refcounts.pop(key, None)
                no_longer_needed.add(key)
            else:
                self._refcounts[key] = count
        return no_longer_needed

    def _check_capacity(
        self, extra: frozenset[str], released: frozenset[str] = frozenset()
    ) -> None:
        after = (set(self._refcounts) | extra) - {
            key for key in released if self._refcounts.get(key, 0) <= 1
        }
        if len(after) > MAX_INSTRUMENTS:
            raise TooManyInstrumentsError(
                f"{len(after)} instruments requested; the feed allows {MAX_INSTRUMENTS}"
            )

    def _rebuild_routes(self) -> None:
        routes: dict[str, list[OnTick]] = {}
        for subscriber in self._subscribers.values():
            for key in subscriber.keys:
                routes.setdefault(key, []).append(subscriber.on_tick)
        self._routes = {key: tuple(callbacks) for key, callbacks in routes.items()}
        self._status_listeners = tuple(
            s.on_status for s in self._subscribers.values() if s.on_status is not None
        )
        self._connection_listeners = tuple(
            s.on_connection for s in self._subscribers.values() if s.on_connection is not None
        )

    # --- data plane (feed thread) ------------------------------------------

    def _on_tick(
        self, key: str, vtt: int | None, ltp: float | None, prev_close: float | None
    ) -> None:
        for callback in self._routes.get(key, ()):
            try:
                callback(key, vtt, ltp, prev_close)
            except Exception:
                self._log_broken(callback)

    def _on_status(self, status: str) -> None:
        self.market_status = status
        for callback in self._status_listeners:
            try:
                callback(status)
            except Exception:
                self._log_broken(callback)

    def _on_connection(self, state: str) -> None:
        self.connection_state = state
        if state == "connected":
            # Covers subscriptions made before the socket opened (which the
            # transport can't send yet) and anything lost across a reconnect.
            with self._lock:
                feed, keys = self._feed, sorted(self._refcounts)
            if feed is not None and keys:
                feed.subscribe(keys)
        for callback in self._connection_listeners:
            try:
                callback(state)
            except Exception:
                self._log_broken(callback)

    def _log_broken(self, callback: object) -> None:
        # Log each failing consumer once, not on every tick.
        marker = id(callback)
        if marker not in self._broken_callbacks:
            self._broken_callbacks.add(marker)
            logger.exception("market data consumer %r raised; other consumers unaffected", callback)
