"""The daily candle cache fetches only what history can actually have that the cache lacks."""

import datetime as dt

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import IST
from swingdash.services.candles import CandleService
from tests.fakes.sources import FakeHistory, FixedCalendar

KEY = "NSE_EQ|INE002A01018"
GANESH_CHATURTHI = dt.date(2026, 9, 14)


def _trading_days(
    start: dt.date, end: dt.date, closed: frozenset[dt.date] = frozenset()
) -> list[DailyBar]:
    bars, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in closed:
            bars.append(DailyBar(day.isoformat(), 100, 101, 99, 100, 1000))
        day += dt.timedelta(days=1)
    return bars


def _service(db: Database, now: dt.datetime, history: FakeHistory) -> CandleService:
    calendar = FixedCalendar(now, closed={GANESH_CHATURTHI})
    return CandleService(history, CandleRepository(db), calendar)


def _at(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=IST)


def test_empty_cache_fetches_the_whole_window_once(db: Database):
    history = FakeHistory(daily={KEY: _trading_days(dt.date(2026, 1, 1), dt.date(2026, 9, 11))})
    candles = _service(db, _at(2026, 9, 13, 10), history)

    bars = candles.daily(KEY, lookback_days=60)

    assert len(history.daily_calls) == 1
    assert bars[-1].date == "2026-09-11"
    candles.daily(KEY, lookback_days=60)
    assert len(history.daily_calls) == 1  # Sunday: Friday is the newest there can be


def test_a_holiday_monday_needs_nothing_new_on_tuesday(db: Database):
    history = FakeHistory(daily={KEY: _trading_days(dt.date(2026, 1, 1), dt.date(2026, 9, 11))})
    _service(db, _at(2026, 9, 12, 10), history).daily(KEY, 60)

    tuesday = _service(db, _at(2026, 9, 15, 11), history)
    assert tuesday.latest_available_session() == dt.date(2026, 9, 11)
    tuesday.daily(KEY, 60)
    assert len(history.daily_calls) == 1


def test_a_cache_behind_by_a_day_fetches_only_the_delta(db: Database):
    all_bars = _trading_days(
        dt.date(2026, 1, 1), dt.date(2026, 9, 15), frozenset({GANESH_CHATURTHI})
    )
    history = FakeHistory(daily={KEY: all_bars[:-1]})  # through Friday 11th
    _service(db, _at(2026, 9, 13, 10), history).daily(KEY, 60)

    history.daily[KEY] = all_bars  # Tuesday's candle is out by Wednesday
    wednesday = _service(db, _at(2026, 9, 16, 9, 30), history)
    assert not wednesday.is_current(KEY)

    bars = wednesday.daily(KEY, 60)

    assert history.daily_calls[-1][1] == dt.date(2026, 9, 12)  # from the day after the cache
    assert bars[-1].date == "2026-09-15"
    assert wednesday.is_current(KEY)


def test_cached_never_calls_the_network(db: Database):
    history = FakeHistory()
    candles = _service(db, _at(2026, 9, 13, 10), history)
    assert candles.cached(KEY, 60) == []
    assert history.daily_calls == []
