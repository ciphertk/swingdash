"""
Interfaces for the seams tests must fake - external data sources and the
feed. Adapters satisfy these structurally; nothing needs to inherit.

Deliberately few: repositories are tested against a real temporary SQLite
database instead, which is both simpler and more faithful than a mock.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from typing import Protocol

from swingdash.domain.bars import DailyBar, MinuteBar
from swingdash.domain.broker import (
    BrokerAccount,
    BrokerDayPosition,
    BrokerHolding,
    BrokerToken,
    BrokerTrade,
)
from swingdash.domain.calendar import Holiday, Session
from swingdash.domain.chartink import ChartinkRequest, ChartinkResult, DashboardDef, ScreenerDef
from swingdash.domain.fundamentals import CompanyProfile
from swingdash.domain.securities import (
    BandEntry,
    Etf,
    Fetched,
    IndexRow,
    ListedEquity,
    Surveillance,
)

OnTick = Callable[[str, int | None, float | None, float | None], None]
OnStatus = Callable[[str], None]
OnConnection = Callable[[str], None]


class HistorySource(Protocol):
    max_minute_span_days: int

    def daily_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[DailyBar]: ...

    def minute_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[MinuteBar]: ...


class ChartinkSourcePort(Protocol):
    """chartink.com screeners, widgets and their pages."""

    def run(self, request: ChartinkRequest, referer: str | None = None) -> ChartinkResult: ...

    def screener(self, url: str) -> ScreenerDef: ...

    def dashboard(self, url: str) -> DashboardDef: ...

    def close(self) -> None: ...


class BrokerSourcePort(Protocol):
    """
    A broker account, read-only. Methods raise BrokerAuthError when the token
    is rejected and BrokerUnavailableError otherwise.
    """

    name: str

    def account(self) -> BrokerAccount: ...

    def holdings(self) -> list[BrokerHolding]: ...

    def day_positions(self) -> list[BrokerDayPosition]: ...

    def trades_today(self) -> list[BrokerTrade]: ...

    def trade_history(self, from_date: dt.date, to_date: dt.date) -> list[BrokerTrade]: ...

    def renew_token(self) -> BrokerToken: ...


class QuoteSource(Protocol):
    def ltp(self, instrument_keys: Sequence[str]) -> dict[str, float]:
        """Last traded price per instrument key (today's, after the close too)."""
        ...


class CalendarSource(Protocol):
    def exchange_bounds(self, date: dt.date) -> tuple[dt.datetime, dt.datetime] | None: ...

    def holidays(self) -> list[Holiday]: ...


class FundamentalsSource(Protocol):
    def company_profile(self, isin: str) -> CompanyProfile: ...


class SecuritiesSource(Protocol):
    """End-of-day NSE reference datasets."""

    def equity_list(self) -> Fetched[list[ListedEquity]]: ...

    def price_bands(self) -> Fetched[list[BandEntry]]: ...

    def surveillance(self, today: dt.date) -> Fetched[dict[str, Surveillance]]: ...

    def etf_list(self) -> Fetched[list[Etf]]: ...

    def indices(self) -> Fetched[list[IndexRow]]: ...

    def close(self) -> None: ...


class MarketCalendar(Protocol):
    def now(self) -> dt.datetime: ...

    def today(self) -> dt.date: ...

    def get_session(self, date: dt.date) -> Session | None: ...

    def active_session(self, now: dt.datetime | None = None) -> Session | None: ...

    def holiday_for(self, date: dt.date | None = None) -> Holiday | None: ...


class FeedTransport(Protocol):
    def start(self, instrument_keys: Sequence[str]) -> None: ...

    def subscribe(self, instrument_keys: Sequence[str]) -> bool: ...

    def unsubscribe(self, instrument_keys: Sequence[str]) -> bool: ...

    def stop(self) -> None: ...


FeedFactory = Callable[[OnTick, OnStatus, OnConnection], FeedTransport]
