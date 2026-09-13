"""
WebSocket market data feed (Upstox MarketDataStreamerV3).

This is the ONLY module that knows the feed's wire shape; everything
downstream sees plain scalars, so a feed change (or a different broker) is
contained here.

Callbacks run on the SDK's own thread - `connect()` spawns `ws.run_forever`
in a background thread - so they must only do thread-safe work.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from typing import Any

import upstox_client

from swingdash.adapters.upstox.client import UpstoxClient

# `full` is the only mode carrying `vtt` (volume traded today); `ltpc`
# omits it, so RVOL is impossible without this mode.
_MODE_FULL = "full"

OnTick = Callable[[str, int | None, float | None, float | None], None]
OnStatus = Callable[[str], None]
OnConnection = Callable[[str], None]


class UpstoxFeedTransport:
    def __init__(
        self,
        client: UpstoxClient,
        on_tick: OnTick,
        on_status: OnStatus | None = None,
        on_connection: OnConnection | None = None,
    ) -> None:
        self._client = client
        self._on_tick = on_tick
        self._on_status = on_status
        self._on_connection = on_connection
        self._streamer: Any = None

    def start(self, instrument_keys: Sequence[str]) -> None:
        streamer = upstox_client.MarketDataStreamerV3(
            self._client.api_client(), list(instrument_keys), _MODE_FULL
        )
        streamer.on("message", self._handle_message)
        streamer.on("open", lambda *_: self._emit_connection("connected"))
        streamer.on("close", lambda *_: self._emit_connection("disconnected"))
        streamer.on("error", lambda *_: self._emit_connection("error"))
        streamer.on("reconnecting", lambda *_: self._emit_connection("reconnecting"))
        streamer.auto_reconnect(True, 5, 10)
        self._streamer = streamer
        streamer.connect()  # non-blocking: runs the socket on its own thread

    def subscribe(self, instrument_keys: Sequence[str]) -> bool:
        """
        Add instruments on the existing connection rather than reconnecting -
        Upstox allows only 2 feed connections per account. False if the
        socket isn't open yet.
        """
        if not instrument_keys or self._streamer is None:
            return False
        try:
            self._streamer.subscribe(list(instrument_keys), _MODE_FULL)
            return True
        except Exception:
            return False

    def unsubscribe(self, instrument_keys: Sequence[str]) -> bool:
        if not instrument_keys or self._streamer is None:
            return False
        try:
            self._streamer.unsubscribe(list(instrument_keys))
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self._streamer is not None:
            # Teardown races are noisy on Windows and harmless.
            with contextlib.suppress(Exception):
                self._streamer.disconnect()

    def _emit_connection(self, state: str) -> None:
        if self._on_connection is not None:
            self._on_connection(state)

    def _handle_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return

        # Exchange state changes arrive as their own message type - more
        # authoritative (and free) than inferring the session from the clock.
        if message.get("type") == "market_info":
            status = ((message.get("marketInfo") or {}).get("segmentStatus") or {}).get("NSE_EQ")
            if status and self._on_status is not None:
                self._on_status(status)
            return

        for key, feed in (message.get("feeds") or {}).items():
            market_ff = (feed.get("fullFeed") or {}).get("marketFF")
            if not market_ff:
                continue  # index feeds carry indexFF and have no vtt
            ltpc = market_ff.get("ltpc") or {}
            self._on_tick(
                key,
                _as_int(market_ff.get("vtt")),
                _as_float(ltpc.get("ltp")),
                _as_float(ltpc.get("cp")),
            )


def _as_int(value: Any) -> int | None:
    # vtt arrives as a string in the protobuf payload.
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
