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
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.bootstrap import build_services
from swingdash.domain.bars import DailyBar
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
# The default Mswing benchmark first; a second index to switch to.
INDEX_KEYS = ("NSE_INDEX|NIFTY MIDSML 400", "NSE_INDEX|NIFTY 50")
DAILY_DRIFTS = {
    "RELIANCE": 0.002,
    "TCS": -0.002,
    "HDFCBANK": 0.004,
    "INFY": 0.0,
    "ICICIBANK": 0.006,
    "SBIN": -0.004,
    "RAYMOND": 0.008,
}


@pytest.fixture(autouse=True)
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Never actually launch a browser in tests; record what would have opened."""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *args, **kwargs: opened.append(url) or True)
    return opened


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
    settings.paths.index_instruments.write_text(
        json.dumps(
            [
                {"instrument_key": k, "trading_symbol": k.split("|")[1], "name": k.split("|")[1]}
                for k in INDEX_KEYS
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
    # Daily candles through Friday for the Scanner - a steady, distinct trend
    # each, so its sort order is predictable. Nothing needs fetching.
    candles = CandleRepository(built.db)
    for symbol, drift in DAILY_DRIFTS.items():
        candles.upsert(key(symbol), daily_history(drift))
    for index_key, drift in zip(INDEX_KEYS, (0.001, 0.003), strict=True):
        candles.upsert(index_key, daily_history(drift))
    yield built
    built.close()


def daily_history(drift: float, through: dt.date = FRIDAY, days: int = 200) -> list[DailyBar]:
    bars, price, day = [], 100.0, through - dt.timedelta(days=days)
    while day <= through:
        if day.weekday() < 5:
            price *= 1 + drift
            bars.append(DailyBar(day.isoformat(), price, price * 1.01, price * 0.99, price, 1e5))
        day += dt.timedelta(days=1)
    return bars
