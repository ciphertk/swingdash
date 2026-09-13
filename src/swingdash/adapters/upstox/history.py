"""
Historical and intraday candles (Upstox History V3 API).

Upstox never includes today's still-open bar in historical results -
verified: after the 2026-09-11 close the newest daily candle was still
09-10. Response candles are `[ts, open, high, low, close, volume, oi]`,
newest first; everything here returns oldest first.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.domain.bars import DailyBar, MinuteBar


class UpstoxHistory:
    # Upstox caps 1-15 minute history at a 1-month range per call, so ~20
    # trading days (about 28 calendar days) is one call per symbol. Wider
    # ranges are rejected rather than truncated.
    max_minute_span_days = 28

    def __init__(self, client: UpstoxClient) -> None:
        self._client = client

    def daily_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[DailyBar]:
        response: Any = self._client.history_v3().get_historical_candle_data1(
            instrument_key, "days", 1, to_date.isoformat(), from_date.isoformat()
        )
        return [
            DailyBar(
                date=row[0][:10],
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in reversed(response.data.candles)
        ]

    def minute_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[MinuteBar]:
        span = (to_date - from_date).days
        if span > self.max_minute_span_days:
            raise ValueError(
                f"minute history range {span}d exceeds Upstox's "
                f"{self.max_minute_span_days}d cap per call"
            )
        response: Any = self._client.history_v3().get_historical_candle_data1(
            instrument_key, "minutes", 1, to_date.isoformat(), from_date.isoformat()
        )
        return _to_minute_bars(response.data.candles)

    def todays_minute_candles(self, instrument_key: str) -> list[MinuteBar]:
        """The current session's 1-minute candles (the historical endpoint excludes today)."""
        response: Any = self._client.history_v3().get_intra_day_candle_data(
            instrument_key, "minutes", 1
        )
        return _to_minute_bars(response.data.candles)


def _to_minute_bars(candles: list[Any]) -> list[MinuteBar]:
    return [
        MinuteBar(ts=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
        for row in reversed(candles)
    ]
