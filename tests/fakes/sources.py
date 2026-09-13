"""In-memory stand-ins for the network seams defined in swingdash.services.ports."""

from __future__ import annotations

import datetime as dt

from swingdash.domain.bars import DailyBar, MinuteBar
from swingdash.domain.calendar import IST, Holiday, Session, regular_session


class FakeCalendarSource:
    """Weekdays trade regular hours unless listed in `closed`; weekends never trade."""

    def __init__(
        self,
        closed: set[dt.date] | None = None,
        holidays: list[Holiday] | None = None,
        fail: bool = False,
    ) -> None:
        self.closed = closed or set()
        self._holidays = holidays or []
        self.fail = fail
        self.bounds_calls: list[dt.date] = []
        self.holiday_calls = 0

    def exchange_bounds(self, date: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        self.bounds_calls.append(date)
        if self.fail:
            raise ConnectionError("offline")
        if date.weekday() >= 5 or date in self.closed:
            return None
        session = regular_session(date)
        return session.open_at, session.close_at

    def holidays(self) -> list[Holiday]:
        self.holiday_calls += 1
        if self.fail:
            raise ConnectionError("offline")
        return list(self._holidays)


class FakeHistory:
    max_minute_span_days = 28

    def __init__(
        self,
        minute: dict[str, list[MinuteBar]] | None = None,
        daily: dict[str, list[DailyBar]] | None = None,
    ) -> None:
        self.minute = minute or {}
        self.daily = daily or {}
        self.minute_calls: list[tuple[str, dt.date, dt.date]] = []

    def daily_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[DailyBar]:
        return [
            bar
            for bar in self.daily.get(instrument_key, [])
            if from_date <= dt.date.fromisoformat(bar.date) <= to_date
        ]

    def minute_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[MinuteBar]:
        self.minute_calls.append((instrument_key, from_date, to_date))
        return [
            bar
            for bar in self.minute.get(instrument_key, [])
            if from_date <= dt.datetime.fromisoformat(bar.ts).astimezone(IST).date() <= to_date
        ]


class FixedCalendar:
    """A MarketCalendar with a controllable clock and every weekday trading."""

    def __init__(self, now: dt.datetime, closed: set[dt.date] | None = None) -> None:
        self.current = now
        self.closed = closed or set()
        self.holidays: dict[dt.date, Holiday] = {}

    def now(self) -> dt.datetime:
        return self.current

    def today(self) -> dt.date:
        return self.current.date()

    def get_session(self, date: dt.date) -> Session | None:
        if date.weekday() >= 5 or date in self.closed:
            return None
        return regular_session(date)

    def active_session(self, now: dt.datetime | None = None) -> Session | None:
        from swingdash.domain.calendar import active_session

        return active_session(now or self.current, self.get_session)

    def holiday_for(self, date: dt.date | None = None) -> Holiday | None:
        return self.holidays.get(date or self.today())


def minute_bars_for_day(date: dt.date, volumes: dict[int, float]) -> list[MinuteBar]:
    """1-minute candles for `date` with the given volume at each minute-of-session."""
    open_at = regular_session(date).open_at
    return [
        MinuteBar(
            ts=(open_at + dt.timedelta(minutes=minute)).isoformat(),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=volume,
        )
        for minute, volume in sorted(volumes.items())
    ]
