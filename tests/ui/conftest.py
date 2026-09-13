"""
Real services wired to fake network seams: no socket, no Upstox, no NSE, a
fixed Sunday clock.
"""

from __future__ import annotations

import datetime as dt
import json
from array import array
from collections.abc import Iterator

import pytest

from swingdash.adapters.storage.repos.baselines import BaselineRepository
from swingdash.bootstrap import build_services
from swingdash.domain.calendar import IST
from swingdash.domain.rvol.types import Baseline
from swingdash.services.container import Services
from swingdash.settings import load_settings
from tests.fakes.feed import FakeFeedFactory
from tests.fakes.sources import (
    FakeCalendarSource,
    FakeFundamentals,
    FakeHistory,
    FakeSecuritiesSource,
)
from tests.ui.helpers import key

SUNDAY = dt.datetime(2026, 9, 13, 2, 0, tzinfo=IST)
FRIDAY = dt.date(2026, 9, 11)
SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "RAYMOND"]


@pytest.fixture
def feed() -> FakeFeedFactory:
    return FakeFeedFactory()


@pytest.fixture
def securities_source() -> FakeSecuritiesSource:
    return FakeSecuritiesSource()


@pytest.fixture
def services(feed: FakeFeedFactory, securities_source: FakeSecuritiesSource) -> Iterator[Services]:
    settings = load_settings()
    settings.paths.ensure()
    settings.paths.equity_instruments.write_text(
        json.dumps(
            [
                {"instrument_key": key(s), "trading_symbol": s, "name": s, "isin": ""}
                for s in SYMBOLS
            ]
        )
    )
    built = build_services(
        settings,
        history=FakeHistory(),
        calendar_source=FakeCalendarSource(),
        fundamentals_source=FakeFundamentals(),
        feed_factory=feed,
        securities_source=securities_source,
        clock=lambda: SUNDAY,
    )
    curve = array("d", (1_000 * (m + 1) / 375 for m in range(375)))
    for symbol in SYMBOLS:
        BaselineRepository(built.db).save(
            key(symbol), FRIDAY, Baseline(curve=curve, avg_full_day_volume=curve[-1], days_used=20)
        )
    built.watchlists.save("nxtDay", ["RAYMOND", "SBIN"])
    yield built
    built.close()
