from __future__ import annotations

import datetime as dt
import time
from array import array
from collections.abc import Callable, Iterator

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.baselines import BaselineRepository
from swingdash.domain.calendar import IST, regular_session
from swingdash.domain.rvol.types import Baseline
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.rvol.baselines import BaselineService
from swingdash.services.rvol.engine import RvolEngine
from tests.fakes.feed import FakeFeedFactory
from tests.fakes.sources import FakeHistory, FixedCalendar, minute_bars_for_day

SUNDAY = dt.datetime(2026, 9, 13, 2, 0, tzinfo=IST)
FRIDAY = dt.date(2026, 9, 11)
KEYS = {"TCS": "K_TCS", "INFY": "K_INFY", "SBIN": "K_SBIN"}


def _flat_baseline(full_day: float) -> Baseline:
    curve = array("d", (full_day * (m + 1) / 375 for m in range(375)))
    return Baseline(curve=curve, avg_full_day_volume=curve[-1], days_used=20)


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


class Harness:
    def __init__(self, db: Database, history: FakeHistory | None = None) -> None:
        self.calendar = FixedCalendar(SUNDAY)
        self.repo = BaselineRepository(db)
        self.history = history or FakeHistory()
        self.baselines = BaselineService(self.history, self.repo, self.calendar)
        self.factory = FakeFeedFactory()
        self.hub = MarketDataHub(self.factory)
        self.engines: list[RvolEngine] = []

    def engine(self, symbols: list[str]) -> RvolEngine:
        engine = RvolEngine(
            symbols,
            calendar=self.calendar,
            baselines=self.baselines,
            resolve_key=KEYS.get,
            hub=self.hub,
        )
        self.engines.append(engine)
        return engine

    def row(self, engine: RvolEngine, symbol: str):
        return next(r for r in engine.snapshot().rows if r.symbol == symbol)


@pytest.fixture
def harness(db: Database) -> Iterator[Harness]:
    h = Harness(db)
    yield h
    for engine in h.engines:
        engine.stop()


def test_weekend_rvol_uses_fridays_close(harness: Harness):
    harness.repo.save("K_TCS", FRIDAY, _flat_baseline(1_000))
    engine = harness.engine(["TCS"])
    engine.start()

    assert harness.factory.transport.started_with == ["K_TCS"]
    harness.factory.transport.tick("K_TCS", vtt=2_000, ltp=110.0, prev_close=100.0)

    _wait_until(lambda: harness.row(engine, "TCS").rvol is not None)
    row = harness.row(engine, "TCS")
    assert engine.snapshot().session_date == FRIDAY
    assert row.rvol == pytest.approx(2.0)
    assert row.rvol_day == pytest.approx(2.0)  # final minute: the two converge
    assert row.change_pct == pytest.approx(10.0)


def test_missing_baselines_are_built_from_history_and_cached(db: Database):
    days = [dt.date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9, 10)]
    history = FakeHistory(
        minute={"K_TCS": [b for day in days for b in minute_bars_for_day(day, {0: 500, 374: 500})]}
    )
    harness = Harness(db, history)
    engine = harness.engine(["TCS"])
    try:
        engine.start()
        _wait_until(lambda: not harness.row(engine, "TCS").degraded)
        assert harness.repo.load_many(["K_TCS"], FRIDAY)["K_TCS"].avg_full_day_volume == 1_000
        # history ends the day before the session it builds for
        assert history.minute_calls[0][2] == dt.date(2026, 9, 10)
    finally:
        engine.stop()


def test_switching_symbols_keeps_retained_state_and_diffs_the_subscription(harness: Harness):
    for key in KEYS.values():
        harness.repo.save(key, FRIDAY, _flat_baseline(1_000))
    engine = harness.engine(["TCS", "INFY"])
    engine.start()
    harness.factory.transport.tick("K_TCS", vtt=1_500)
    _wait_until(lambda: harness.row(engine, "TCS").volume == 1_500)

    engine.set_symbols(["TCS", "SBIN"])

    assert harness.row(engine, "TCS").volume == 1_500  # not reset by the switch
    assert {r.symbol for r in engine.snapshot().rows} == {"TCS", "SBIN"}
    assert harness.factory.transport.unsubscribed == [["K_INFY"]]
    assert harness.factory.transport.subscribed == [["K_SBIN"]]


def test_session_rollover_clears_volume_so_the_open_is_not_a_fake_spike(harness: Harness):
    tuesday = regular_session(dt.date(2026, 9, 15))
    harness.repo.save("K_TCS", FRIDAY, _flat_baseline(1_000))
    harness.repo.save("K_TCS", tuesday.date, _flat_baseline(2_000))
    engine = harness.engine(["TCS"])
    engine.start()
    harness.factory.transport.tick("K_TCS", vtt=9_000)
    _wait_until(lambda: harness.row(engine, "TCS").volume == 9_000)

    engine._roll_session(tuesday)

    row = harness.row(engine, "TCS")
    assert row.volume is None
    assert engine.snapshot().session_date == tuesday.date


def test_baseline_fetched_for_an_old_session_is_not_applied_after_rollover(db: Database):
    harness = Harness(db)
    engine = harness.engine(["TCS"])
    tuesday = regular_session(dt.date(2026, 9, 15))
    day_bars = minute_bars_for_day(dt.date(2026, 9, 10), {0: 100})

    def fetch_during_rollover(key, from_date, to_date):
        engine._session = tuesday  # the open happens while this request is in flight
        return day_bars

    harness.history.minute_candles = fetch_during_rollover  # type: ignore[method-assign]
    engine._build_missing(["K_TCS"])

    assert engine._states["K_TCS"].baseline is None
    assert "K_TCS" in harness.repo.load_many(["K_TCS"], FRIDAY)  # still cached for its own day


def test_unknown_symbols_are_reported_not_fatal(harness: Harness):
    engine = harness.engine(["TCS", "NOPE"])
    engine.start()
    assert "unknown symbol: NOPE" in engine.events()
    assert [r.symbol for r in engine.snapshot().rows] == ["TCS"]


def test_stopping_an_engine_releases_instruments_but_keeps_the_shared_feed(harness: Harness):
    engine = harness.engine(["TCS", "INFY"])
    engine.start()
    engine.stop()
    assert harness.hub.instrument_count == 0
    assert not harness.factory.transport.stopped
