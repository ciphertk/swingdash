"""
NSE session calendar - the single source of truth for "minute 0".

Nothing else should hardcode 09:15. Session bounds come from the exchange
timings API (which handles holidays and special sessions such as muhurat
trading) and are cached one row per date, so each date costs at most one
API call.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable

from swingdash.adapters.storage.repos.sessions import SessionRepository
from swingdash.domain.calendar import Holiday, Session, active_session, now_ist, regular_session
from swingdash.services.ports import CalendarSource

logger = logging.getLogger(__name__)


class CalendarService:
    def __init__(
        self,
        source: CalendarSource,
        repo: SessionRepository,
        clock: Callable[[], dt.datetime] = now_ist,
    ) -> None:
        self._source = source
        self._repo = repo
        self._clock = clock
        self._holidays: dict[dt.date, Holiday] | None = None
        self._holidays_lock = threading.Lock()

    def now(self) -> dt.datetime:
        return self._clock()

    def today(self) -> dt.date:
        return self._clock().date()

    def get_session(self, date: dt.date) -> Session | None:
        """Session bounds for `date`, or None if NSE didn't trade that day."""
        found, cached = self._repo.read(date)
        if found:
            return cached

        try:
            bounds = self._source.exchange_bounds(date)
        except Exception:
            # Don't take the engine down over the network: assume regular
            # hours on a weekday, closed at the weekend, and don't cache so
            # the next lookup retries.
            logger.warning("exchange timings unavailable for %s", date, exc_info=True)
            return regular_session(date) if date.weekday() < 5 else None

        # A trading day is final once published. "Not a trading day" is only
        # persisted for past dates, so a special session announced for today
        # can't be masked by an answer cached earlier in the day.
        if bounds is not None or date < self.today():
            self._repo.write(date, bounds)
        if bounds is None:
            return None
        return Session(date=date, open_at=bounds[0], close_at=bounds[1])

    def active_session(self, now: dt.datetime | None = None) -> Session | None:
        """The last session that has opened. See domain.calendar.active_session."""
        return active_session(now or self.now(), self.get_session)

    def holiday_for(self, date: dt.date | None = None) -> Holiday | None:
        return self._load_holidays().get(date or self.today())

    def _load_holidays(self) -> dict[dt.date, Holiday]:
        if self._holidays is not None:
            return self._holidays
        with self._holidays_lock:
            if self._holidays is None:
                try:
                    self._holidays = {h.date: h for h in self._source.holidays()}
                except Exception:
                    # Not cached, so it retries next time.
                    logger.warning("holiday calendar unavailable", exc_info=True)
                    return {}
            return self._holidays
