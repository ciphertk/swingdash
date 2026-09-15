"""A BrokerSourcePort with scripted holdings, fills and token behaviour."""

from __future__ import annotations

import datetime as dt

from swingdash.domain.broker import (
    BrokerAccount,
    BrokerDayPosition,
    BrokerHolding,
    BrokerToken,
    BrokerTrade,
)
from swingdash.domain.errors import BrokerAuthError


class FakeBroker:
    name = "dhan"

    def __init__(self, valid_until: dt.datetime | None = None) -> None:
        self.valid_until = valid_until
        self.history: list[BrokerTrade] = []  # everything before today
        self.today: list[BrokerTrade] = []
        self.holdings_list: list[BrokerHolding] = []
        self.rejected = False  # the token is expired / wrong
        self.renewed_to: BrokerToken | None = None
        self.calls: list[str] = []
        self.history_ranges: list[tuple[dt.date, dt.date]] = []

    def _check(self, call: str) -> None:
        self.calls.append(call)
        if self.rejected:
            raise BrokerAuthError(
                "Dhan: access token is invalid or expired - generate a new token on web.dhan.co."
            )

    def account(self) -> BrokerAccount:
        self._check("account")
        return BrokerAccount("1000000000", "TEST USER", self.valid_until)

    def holdings(self) -> list[BrokerHolding]:
        self._check("holdings")
        return list(self.holdings_list)

    def day_positions(self) -> list[BrokerDayPosition]:
        self._check("positions")
        return []

    def trades_today(self) -> list[BrokerTrade]:
        self._check("trades_today")
        return list(self.today)

    def trade_history(self, from_date: dt.date, to_date: dt.date) -> list[BrokerTrade]:
        self._check("trade_history")
        self.history_ranges.append((from_date, to_date))
        return [t for t in self.history if from_date <= t.time.date() <= to_date]

    def renew_token(self) -> BrokerToken:
        self._check("renew")
        assert self.renewed_to is not None, "renewal wasn't scripted"
        return self.renewed_to
