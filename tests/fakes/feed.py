"""A controllable FeedTransport for driving the hub and engine without a socket."""

from __future__ import annotations

from collections.abc import Sequence

from swingdash.services.ports import OnConnection, OnStatus, OnTick


class FakeFeedTransport:
    def __init__(self, on_tick: OnTick, on_status: OnStatus, on_connection: OnConnection) -> None:
        self._on_tick = on_tick
        self._on_status = on_status
        self._on_connection = on_connection
        self.started_with: list[str] | None = None
        self.subscribed: list[list[str]] = []
        self.unsubscribed: list[list[str]] = []
        self.stopped = False
        self.open = False

    # FeedTransport
    def start(self, instrument_keys: Sequence[str]) -> None:
        self.started_with = list(instrument_keys)

    def subscribe(self, instrument_keys: Sequence[str]) -> bool:
        self.subscribed.append(list(instrument_keys))
        return self.open

    def unsubscribe(self, instrument_keys: Sequence[str]) -> bool:
        self.unsubscribed.append(list(instrument_keys))
        return self.open

    def stop(self) -> None:
        self.stopped = True

    # test drivers
    def connect(self) -> None:
        self.open = True
        self._on_connection("connected")

    def tick(
        self,
        key: str,
        vtt: int | None = None,
        ltp: float | None = None,
        prev_close: float | None = None,
    ) -> None:
        self._on_tick(key, vtt, ltp, prev_close)

    def status(self, status: str) -> None:
        self._on_status(status)


class FakeFeedFactory:
    def __init__(self) -> None:
        self.created: list[FakeFeedTransport] = []

    def __call__(
        self, on_tick: OnTick, on_status: OnStatus, on_connection: OnConnection
    ) -> FakeFeedTransport:
        transport = FakeFeedTransport(on_tick, on_status, on_connection)
        self.created.append(transport)
        return transport

    @property
    def transport(self) -> FakeFeedTransport:
        assert len(self.created) == 1, f"expected exactly one feed, got {len(self.created)}"
        return self.created[0]
