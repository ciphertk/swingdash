"""Dhan as a BrokerSourcePort: the account's holdings, positions and fills, read-only."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from swingdash.adapters.dhan import parsers
from swingdash.adapters.dhan.http import DhanCredentials, DhanHttp
from swingdash.domain.broker import (
    BrokerAccount,
    BrokerDayPosition,
    BrokerHolding,
    BrokerToken,
    BrokerTrade,
)
from swingdash.domain.errors import BrokerAuthError

# Trade history is read in windows of this many days, page by page.
HISTORY_WINDOW_DAYS = 90
_MAX_PAGES = 500


class DhanSource:
    name = "dhan"

    def __init__(
        self, credentials: Callable[[], DhanCredentials | None], http: DhanHttp | None = None
    ) -> None:
        self._credentials = credentials
        self._http = http or DhanHttp(credentials)

    def account(self) -> BrokerAccount:
        return parsers.parse_account(self._http.get("profile"), self._token())

    def holdings(self) -> list[BrokerHolding]:
        return parsers.parse_holdings(self._http.get("holdings"))

    def day_positions(self) -> list[BrokerDayPosition]:
        return parsers.parse_positions(self._http.get("positions"))

    def trades_today(self) -> list[BrokerTrade]:
        return parsers.parse_trades(self._http.get("trades"))

    def trade_history(self, from_date: dt.date, to_date: dt.date) -> list[BrokerTrade]:
        trades: list[BrokerTrade] = []
        start = from_date
        while start <= to_date:
            end = min(to_date, start + dt.timedelta(days=HISTORY_WINDOW_DAYS - 1))
            for page in range(_MAX_PAGES):
                batch = parsers.parse_trades(
                    self._http.get(f"trades/{start.isoformat()}/{end.isoformat()}/{page}")
                )
                if not batch:
                    break
                trades.extend(batch)
            start = end + dt.timedelta(days=1)
        return trades

    def renew_token(self) -> BrokerToken:
        credentials = self._credentials()
        if credentials is None:
            raise BrokerAuthError("Dhan isn't connected - paste an access token first.")
        payload = self._http.get("RenewToken", headers={"dhanClientId": credentials.client_id})
        return parsers.parse_renewed_token(payload)

    def _token(self) -> str:
        credentials = self._credentials()
        return credentials.access_token if credentials else ""
