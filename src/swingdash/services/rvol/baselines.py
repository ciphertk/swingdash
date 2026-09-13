"""Builds and caches per-symbol RVOL baselines."""

from __future__ import annotations

import datetime as dt

from swingdash.adapters.storage.repos.baselines import BaselineRepository
from swingdash.domain.calendar import Session
from swingdash.domain.rvol.curve import DEFAULT_BASELINE_DAYS, baseline_from_bars
from swingdash.domain.rvol.types import Baseline
from swingdash.services.ports import HistorySource, MarketCalendar


class BaselineService:
    def __init__(
        self,
        history: HistorySource,
        repo: BaselineRepository,
        calendar: MarketCalendar,
        days: int = DEFAULT_BASELINE_DAYS,
    ) -> None:
        self._history = history
        self._repo = repo
        self._calendar = calendar
        self._days = days

    def build(self, instrument_key: str, session: Session) -> Baseline | None:
        """
        One history call per symbol: the widest window the source allows for
        1-minute candles (~28 calendar days, enough for 20 sessions). Returns
        None if the symbol has no usable history.
        """
        to_date = session.date - dt.timedelta(days=1)  # history excludes today
        from_date = to_date - dt.timedelta(days=self._history.max_minute_span_days - 1)
        bars = self._history.minute_candles(instrument_key, from_date, to_date)
        if not bars:
            return None
        return baseline_from_bars(bars, session, self._calendar.get_session, self._days)

    def load_many(self, instrument_keys: list[str], session_date: dt.date) -> dict[str, Baseline]:
        return self._repo.load_many(instrument_keys, session_date)

    def save(self, instrument_key: str, session_date: dt.date, baseline: Baseline) -> None:
        self._repo.save(instrument_key, session_date, baseline)
