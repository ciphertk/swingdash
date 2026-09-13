"""Market quotes (Upstox Market Quote V3 API)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import IST

# Full Market Quote V3 accepts at most 500 comma-separated instrument keys
# (https://upstox.com/developer/api-documentation/get-full-market-quote-v3).
MAX_QUOTE_INSTRUMENTS_PER_REQUEST = 500


class UpstoxQuotes:
    def __init__(self, client: UpstoxClient) -> None:
        self._client = client

    def ltp(self, instrument_keys: Sequence[str]) -> dict[str, float]:
        """Last traded price per instrument key - also the cheapest token check."""
        api = self._client.market_quote_v3()
        response: Any = self._client.call(api.get_ltp, instrument_key=",".join(instrument_keys))
        return {
            quote.instrument_token: float(quote.last_price)
            for quote in (response.data or {}).values()
        }

    def live_bars(self, instrument_keys: Sequence[str]) -> dict[str, DailyBar]:
        """
        Today's still-forming bar per instrument key, batched into as few
        calls as the 500-key cap allows. Keyed by instrument key - Upstox
        keys its response by "EXCHANGE:SYMBOL", remapped here via each
        quote's `instrument_token`.
        """
        if not instrument_keys:
            return {}

        today = dt.datetime.now(IST).date().isoformat()
        bars: dict[str, DailyBar] = {}
        for i in range(0, len(instrument_keys), MAX_QUOTE_INSTRUMENTS_PER_REQUEST):
            chunk = instrument_keys[i : i + MAX_QUOTE_INSTRUMENTS_PER_REQUEST]
            # One accessor per request, so each takes its own rate-limit slot.
            api = self._client.market_quote_v3()
            response: Any = self._client.call(
                api.get_full_market_quote_v3, instrument_key=",".join(chunk)
            )
            for quote in response.data.values():
                ohlc = quote.ohlc
                bars[quote.instrument_token] = DailyBar(
                    date=today,
                    open=ohlc.open,
                    high=ohlc.high,
                    low=ohlc.low,
                    close=ohlc.close,
                    volume=quote.volume,
                )
        return bars
