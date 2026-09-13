"""ScannerEngine against a real hub, SQLite cache and fake history/feed."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Iterator

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import IST
from swingdash.domain.scanner import ScannerSnapshot
from swingdash.services.candles import CandleService
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.scanner.engine import ScannerEngine
from tests.fakes.feed import FakeFeedFactory
from tests.fakes.sources import FakeHistory, FixedCalendar

INDEX_KEY = "NSE_INDEX|NIFTY MIDSML 400"
FRI, MON = dt.date(2026, 9, 11), dt.date(2026, 9, 15)
GANESH_CHATURTHI = dt.date(2026, 9, 14)


def _key(symbol: str) -> str:
    return f"NSE_EQ|{symbol}"


def _history(through: dt.date, drift: float, days: int = 400) -> list[DailyBar]:
    bars, price, day = [], 100.0, through - dt.timedelta(days=days)
    while day <= through:
        if day.weekday() < 5 and day != GANESH_CHATURTHI:
            price *= 1 + drift
            bars.append(DailyBar(day.isoformat(), price, price * 1.01, price * 0.99, price, 1e5))
        day += dt.timedelta(days=1)
    return bars


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


class Harness:
    def __init__(self, db: Database, now: dt.datetime) -> None:
        self.calendar = FixedCalendar(now, closed={GANESH_CHATURTHI})
        self.history = FakeHistory()
        self.repo = CandleRepository(db)
        self.feed = FakeFeedFactory()
        self.hub = MarketDataHub(self.feed)
        self.engine: ScannerEngine | None = None

    def cache(self, symbol_key: str, bars: list[DailyBar]) -> None:
        self.repo.upsert(symbol_key, bars)

    def start(self, symbols: list[str]) -> ScannerEngine:
        self.engine = ScannerEngine(
            symbols,
            calendar=self.calendar,
            candles=CandleService(self.history, self.repo, self.calendar),
            resolve_key=lambda s: None if s == "BOGUS" else _key(s),
            hub=self.hub,
            index_key=INDEX_KEY,
            index_name="NIFTY MIDSML 400",
        )
        self.engine.start()
        return self.engine

    def snapshot(self) -> ScannerSnapshot:
        assert self.engine is not None
        return self.engine.snapshot()

    def row(self, symbol: str):
        return next(r for r in self.snapshot().rows if r.symbol == symbol)

    def close(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        self.hub.close()


@pytest.fixture
def harness(db: Database) -> Iterator[Harness]:
    h = Harness(db, dt.datetime(2026, 9, 15, 11, 0, tzinfo=IST))  # Tuesday, market open
    yield h
    h.close()


def _prime(h: Harness) -> None:
    """Cache complete through Friday - the last session before Tuesday (Monday was a holiday)."""
    h.cache(_key("RAYMOND"), _history(FRI, drift=0.004))
    h.cache(_key("TCS"), _history(FRI, drift=-0.002))
    h.cache(INDEX_KEY, _history(FRI, drift=0.001))


def test_rows_come_from_the_cache_without_any_history_calls(harness: Harness):
    _prime(harness)
    harness.start(["RAYMOND", "TCS", "BOGUS"])

    _wait(lambda: harness.snapshot().loaded == 2)

    snap = harness.snapshot()
    assert harness.history.daily_calls == []
    assert snap.total == 2
    assert snap.index.metrics is not None
    assert harness.feed.transport.started_with is not None
    assert set(harness.feed.transport.started_with) == {_key("RAYMOND"), _key("TCS"), INDEX_KEY}
    raymond = harness.row("RAYMOND")
    assert raymond.history_through == FRI
    assert raymond.mswing_class == "strong"  # rising steadily, faster than the index
    assert harness.row("TCS").mswing_class == "weak"


def test_ticks_move_mswing_live_but_not_burst_power(harness: Harness):
    _prime(harness)
    harness.start(["RAYMOND"])
    _wait(lambda: harness.snapshot().loaded == 1 and harness.snapshot().index.metrics is not None)
    before = harness.row("RAYMOND")
    assert before.metrics is not None and not before.metrics.includes_today

    last_close = _history(FRI, drift=0.004)[-1].close
    harness.feed.transport.tick(_key("RAYMOND"), ltp=last_close * 1.12)
    harness.feed.transport.tick(INDEX_KEY, ltp=_history(FRI, drift=0.001)[-1].close)

    during = harness.row("RAYMOND")
    assert during.metrics is not None and during.metrics.includes_today
    assert during.metrics.mswing.score != before.metrics.mswing.score
    assert during.metrics.burst == before.metrics.burst
    assert during.change_pct == pytest.approx(12.0)

    harness.calendar.current = dt.datetime(2026, 9, 15, 15, 45, tzinfo=IST)  # after the close
    after = harness.row("RAYMOND")
    assert after.metrics is not None
    assert after.metrics.burst.count_10pct == before.metrics.burst.count_10pct + 1


def test_a_stale_cache_is_topped_up_in_the_background(harness: Harness):
    through_thu = _history(dt.date(2026, 9, 10), drift=0.004)
    harness.cache(_key("RAYMOND"), through_thu)
    harness.cache(INDEX_KEY, _history(FRI, drift=0.001))
    harness.history.daily[_key("RAYMOND")] = _history(FRI, drift=0.004)

    harness.start(["RAYMOND"])

    _wait(lambda: harness.row("RAYMOND").history_through == FRI)
    assert [call[0] for call in harness.history.daily_calls] == [_key("RAYMOND")]
    _wait(lambda: not harness.snapshot().fetching)


def test_a_failed_fetch_keeps_the_cached_rows_and_reports_it(harness: Harness):
    harness.cache(_key("RAYMOND"), _history(dt.date(2026, 9, 10), drift=0.004))
    harness.cache(INDEX_KEY, _history(FRI, drift=0.001))
    harness.history.failing_daily.add(_key("RAYMOND"))
    engine = harness.start(["RAYMOND"])

    _wait(lambda: not harness.snapshot().fetching and harness.history.daily_calls != [])

    assert harness.row("RAYMOND").history_through == dt.date(2026, 9, 10)
    assert any("history failed" in event for event in engine.events())


def test_a_rate_limited_fetch_is_retried_not_dropped(harness: Harness):
    harness.cache(_key("RAYMOND"), _history(dt.date(2026, 9, 10), drift=0.004))
    harness.cache(INDEX_KEY, _history(FRI, drift=0.001))
    harness.history.daily[_key("RAYMOND")] = _history(FRI, drift=0.004)
    harness.history.rate_limited_daily_once.add(_key("RAYMOND"))
    engine = harness.start(["RAYMOND"])

    _wait(lambda: harness.row("RAYMOND").history_through == FRI)

    assert [call[0] for call in harness.history.daily_calls] == [_key("RAYMOND")] * 2
    assert "Upstox rate limit - history paused, retrying" in engine.events()
    _wait(lambda: not harness.snapshot().fetching)


def test_switching_symbols_and_index(harness: Harness):
    _prime(harness)
    harness.cache("NSE_INDEX|NIFTY 50", _history(FRI, drift=0.003))
    engine = harness.start(["RAYMOND"])
    _wait(lambda: harness.snapshot().loaded == 1)

    engine.set_symbols(["RAYMOND", "TCS"])
    _wait(lambda: harness.snapshot().loaded == 2)
    assert [_key("TCS")] in harness.feed.transport.subscribed

    engine.set_index("NSE_INDEX|NIFTY 50", "NIFTY 50")
    _wait(lambda: harness.snapshot().index.metrics is not None)
    snap = harness.snapshot()
    assert snap.index.name == "NIFTY 50"
    assert [INDEX_KEY] in harness.feed.transport.unsubscribed


def test_weekend_shows_fridays_numbers(db: Database):
    h = Harness(db, dt.datetime(2026, 9, 13, 2, 0, tzinfo=IST))  # Sunday
    try:
        _prime(h)
        h.start(["RAYMOND"])
        _wait(lambda: h.snapshot().loaded == 1)
        h.feed.transport.tick(_key("RAYMOND"), ltp=_history(FRI, drift=0.004)[-1].close)
        row = h.row("RAYMOND")
        assert h.snapshot().session_date == FRI
        assert row.metrics is not None and not row.metrics.includes_today
        assert h.history.daily_calls == []
    finally:
        h.close()
