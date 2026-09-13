import datetime as dt

import pytest

from swingdash.domain.calendar import IST, active_session, latest_publish_cutoff, regular_session

GANESH_CHATURTHI = dt.date(2026, 9, 14)  # Monday, NSE trading holiday


def _lookup(date: dt.date):
    if date.weekday() >= 5 or date == GANESH_CHATURTHI:
        return None
    return regular_session(date)


def _at(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=IST)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(2026, 9, 13, 2, 7), dt.date(2026, 9, 11)),  # Sunday -> Friday
        (_at(2026, 9, 14, 11, 0), dt.date(2026, 9, 11)),  # holiday -> Friday
        (_at(2026, 9, 15, 9, 14, 59), dt.date(2026, 9, 11)),  # pre-open -> last session
        (_at(2026, 9, 15, 9, 15, 0), dt.date(2026, 9, 15)),  # the open itself
        (_at(2026, 9, 11, 18, 0), dt.date(2026, 9, 11)),  # after close -> today
    ],
)
def test_active_session_is_the_last_session_that_has_opened(now, expected):
    session = active_session(now, _lookup)
    assert session is not None
    assert session.date == expected


def test_minute_of_session_bounds():
    session = regular_session(dt.date(2026, 9, 15))
    assert session.minutes == 375
    assert session.minute_of(_at(2026, 9, 15, 9, 14)) is None
    assert session.minute_of(_at(2026, 9, 15, 9, 15)) == 0
    assert session.minute_of(_at(2026, 9, 15, 15, 29)) == 374
    assert session.minute_of(_at(2026, 9, 15, 17, 0)) == 374  # clamped past the close


def test_no_session_within_lookback_returns_none():
    assert active_session(_at(2026, 9, 15, 12, 0), lambda _d: None) is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(2026, 9, 11, 21, 29), dt.datetime(2026, 9, 10, 21, 30, tzinfo=IST)),  # Fri, not yet
        (_at(2026, 9, 11, 21, 31), dt.datetime(2026, 9, 11, 21, 30, tzinfo=IST)),  # Fri, published
        (_at(2026, 9, 13, 2, 0), dt.datetime(2026, 9, 11, 21, 30, tzinfo=IST)),  # Sunday
        (_at(2026, 9, 14, 23, 0), dt.datetime(2026, 9, 11, 21, 30, tzinfo=IST)),  # holiday
    ],
)
def test_latest_publish_cutoff_is_the_last_trading_evening(now, expected):
    assert latest_publish_cutoff(now, _lookup) == expected
