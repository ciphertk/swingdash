import datetime as dt

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.sessions import SessionRepository
from swingdash.domain.calendar import IST, Holiday
from swingdash.services.calendar import CalendarService
from tests.fakes.sources import FakeCalendarSource

SUNDAY_NOW = dt.datetime(2026, 9, 13, 2, 0, tzinfo=IST)
SATURDAY = dt.date(2026, 9, 12)
FRIDAY = dt.date(2026, 9, 11)


def _service(db: Database, source: FakeCalendarSource, now: dt.datetime = SUNDAY_NOW):
    return CalendarService(source, SessionRepository(db), clock=lambda: now)


def test_past_non_trading_days_are_served_from_cache(db: Database):
    source = FakeCalendarSource()
    calendar = _service(db, source)
    assert calendar.get_session(SATURDAY) is None
    assert calendar.get_session(SATURDAY) is None
    assert source.bounds_calls == [SATURDAY]


def test_todays_non_trading_answer_is_not_persisted(db: Database):
    source = FakeCalendarSource()
    calendar = _service(db, source)
    today = SUNDAY_NOW.date()
    calendar.get_session(today)
    calendar.get_session(today)
    assert source.bounds_calls == [today, today]


def test_active_session_on_a_weekend_is_friday(db: Database):
    session = _service(db, FakeCalendarSource()).active_session()
    assert session is not None
    assert session.date == FRIDAY


def test_offline_assumes_regular_hours_only_on_weekdays(db: Database):
    calendar = _service(db, FakeCalendarSource(fail=True))
    assert calendar.get_session(SATURDAY) is None
    weekday = calendar.get_session(FRIDAY)
    assert weekday is not None
    assert weekday.open_at.time() == dt.time(9, 15)


def test_holidays_load_once_and_failures_are_retried(db: Database):
    holiday = Holiday(dt.date(2026, 9, 14), "TRADING_HOLIDAY", "Ganesh Chaturthi", nse_closed=True)
    source = FakeCalendarSource(holidays=[holiday], fail=True)
    calendar = _service(db, source)

    assert calendar.holiday_for(holiday.date) is None  # offline: not cached
    source.fail = False
    assert calendar.holiday_for(holiday.date) == holiday
    assert calendar.holiday_for(holiday.date) == holiday
    assert source.holiday_calls == 2
