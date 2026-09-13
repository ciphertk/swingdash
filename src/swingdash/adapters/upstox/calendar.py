"""Exchange timings and holidays (Upstox Market Information API)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.domain.calendar import IST, Holiday

_EXCHANGE = "NSE"


class UpstoxCalendarSource:
    def __init__(self, client: UpstoxClient) -> None:
        self._client = client

    def exchange_bounds(self, date: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        """NSE open/close for `date`, or None when NSE doesn't trade that day."""
        api = self._client.market_calendar()
        response: Any = self._client.call(api.get_exchange_timings, date.isoformat())
        for entry in response.data or []:
            if entry.exchange == _EXCHANGE:
                return (
                    dt.datetime.fromtimestamp(int(entry.start_time) / 1000, IST),
                    dt.datetime.fromtimestamp(int(entry.end_time) / 1000, IST),
                )
        return None

    def holidays(self) -> list[Holiday]:
        api = self._client.market_calendar()
        response: Any = self._client.call(api.get_holidays)
        holidays: list[Holiday] = []
        for entry in response.data or []:
            # The SDK exposes the date only under its mangled attribute name.
            raw_date = getattr(entry, "_date", None)
            if raw_date is None:
                continue
            day = raw_date.date() if isinstance(raw_date, dt.datetime) else raw_date
            holidays.append(
                Holiday(
                    date=day,
                    holiday_type=entry.holiday_type or "",
                    description=entry.description or "",
                    nse_closed=_EXCHANGE in (entry.closed_exchanges or []),
                )
            )
        return holidays
