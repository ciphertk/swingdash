"""
Builds Upstox SDK API objects. All authentication goes through here, so if
auth ever changes (e.g. a second, order-placing OAuth token alongside the
read-only Analytics Token) it changes in one place.
"""

from __future__ import annotations

from collections.abc import Callable

import upstox_client


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
