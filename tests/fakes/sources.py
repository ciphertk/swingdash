"""In-memory stand-ins for the network seams defined in swingdash.services.ports."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from swingdash.adapters.nse import parsers
from swingdash.domain.bars import DailyBar, MinuteBar
from swingdash.domain.calendar import IST, Holiday, Session, regular_session
from swingdash.domain.errors import RateLimitedError
from swingdash.domain.fundamentals import CompanyProfile
from swingdash.domain.securities import (
    BandEntry,
    Etf,
    Fetched,
    IndexRow,
    ListedEquity,
    Surveillance,
)


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
        self.daily_calls: list[tuple[str, dt.date, dt.date]] = []
        self.failing_daily: set[str] = set()
        self.rate_limited_daily_once: set[str] = set()

    def daily_candles(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[DailyBar]:
        self.daily_calls.append((instrument_key, from_date, to_date))
        if instrument_key in self.failing_daily:
            raise ConnectionError("offline")
        if instrument_key in self.rate_limited_daily_once:
            self.rate_limited_daily_once.discard(instrument_key)
            raise RateLimitedError("429")
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


_NSE_FIXTURES = Path(__file__).parents[1] / "fixtures" / "nse"


class FakeSecuritiesSource:
    """Serves the captured NSE files through the real parsers; datasets can be made to fail."""

    AS_OF = dt.date(2026, 9, 11)

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[str] = []
        self.closed = False

    def _read(self, name: str) -> str:
        return (_NSE_FIXTURES / name).read_text(encoding="utf-8")

    def _call(self, dataset: str) -> None:
        self.calls.append(dataset)
        if dataset in self.failing:
            raise ConnectionError(f"{dataset} offline")

    def equity_list(self) -> Fetched[list[ListedEquity]]:
        self._call("listings")
        return Fetched(parsers.parse_equity_list(self._read("EQUITY_L.csv")), self.AS_OF)

    def price_bands(self) -> Fetched[list[BandEntry]]:
        self._call("bands")
        return Fetched(parsers.parse_price_bands(self._read("sec_list.csv")), self.AS_OF)

    def surveillance(self, today: dt.date) -> Fetched[dict[str, Surveillance]]:
        self._call("surveillance")
        stages, as_of = parsers.parse_surveillance_reports(
            json.loads(self._read("reportASM.json")),
            json.loads(self._read("reportGSM.json")),
            json.loads(self._read("reportESM.json")),
        )
        return Fetched(stages, as_of)

    def etf_list(self) -> Fetched[list[Etf]]:
        self._call("etfs")
        return Fetched(parsers.parse_etf_list(self._read("eq_etfseclist.csv")), self.AS_OF)

    def indices(self) -> Fetched[list[IndexRow]]:
        self._call("indices")
        rows, as_of = parsers.parse_all_indices(json.loads(self._read("allIndices.json")))
        return Fetched(rows, as_of)

    def close(self) -> None:
        self.closed = True


class FakeFundamentals:
    """Sector = "Sector of <ISIN>"; ISINs in `rate_limited_once` answer 429 the first time."""

    def __init__(
        self,
        empty: set[str] | None = None,
        failing: set[str] | None = None,
        rate_limited_once: set[str] | None = None,
    ) -> None:
        self.empty = empty or set()
        self.failing = failing or set()
        self.rate_limited_once = set(rate_limited_once or ())
        self.calls: list[str] = []

    def company_profile(self, isin: str) -> CompanyProfile:
        self.calls.append(isin)
        if isin in self.rate_limited_once:
            self.rate_limited_once.discard(isin)
            raise RateLimitedError("429")
        if isin in self.failing:
            raise ConnectionError("offline")
        if isin in self.empty:
            return CompanyProfile(None, None, None)
        return CompanyProfile(f"Sector of {isin}", 1000.0, None)
