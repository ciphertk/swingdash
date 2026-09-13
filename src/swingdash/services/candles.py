"""
Daily candles with a local cache.

A completed trading day's candle never changes, so only candles newer than
the last cached date are fetched. Upstox's history never includes today,
so the newest candle there can be is the last trading session BEFORE
today - once the cache has that, there is nothing to fetch. One small
delta call per symbol per trading day, and none on weekends or on later
opens the same day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import MAX_LOOKBACK_DAYS
from swingdash.services.ports import HistorySource, MarketCalendar

# One network round trip per symbol (no batch endpoint). The shared rate
# limiter in the Upstox adapter is what actually keeps this under quota.
MAX_CONCURRENT_HISTORY_FETCHES = 10


class CandleService:
    def __init__(
        self, history: HistorySource, repo: CandleRepository, calendar: MarketCalendar
    ) -> None:
        self._history = history
        self._repo = repo
        self._calendar = calendar

    def cached(self, instrument_key: str, lookback_days: int) -> list[DailyBar]:
        """Whatever the cache already holds for the window - never a network call."""
        today = self._calendar.today()
        return self._repo.read_range(
            instrument_key, today - dt.timedelta(days=lookback_days), today
        )

    def is_current(self, instrument_key: str) -> bool:
        """True when the cache already has every candle history can offer."""
        latest = self._repo.latest_date(instrument_key)
        wanted = self.latest_available_session()
        return latest is not None and (wanted is None or latest >= wanted)

    def daily(self, instrument_key: str, lookback_days: int) -> list[DailyBar]:
        """Completed daily candles for the window, fetching only what the cache lacks."""
        today = self._calendar.today()
        from_date = today - dt.timedelta(days=lookback_days)

        latest = self._repo.latest_date(instrument_key)
        if latest is None:
            self._repo.upsert(
                instrument_key, self._history.daily_candles(instrument_key, from_date, today)
            )
        elif not self.is_current(instrument_key):
            delta = self._history.daily_candles(
                instrument_key, latest + dt.timedelta(days=1), today
            )
            self._repo.upsert(instrument_key, delta)

        return self._repo.read_range(instrument_key, from_date, today)

    def latest_available_session(self) -> dt.date | None:
        """The last trading session before today - the newest candle history can hold."""
        day = self._calendar.today()
        for _ in range(MAX_LOOKBACK_DAYS):
            day -= dt.timedelta(days=1)
            if self._calendar.get_session(day) is not None:
                return day
        return None

    def daily_bulk(
        self, instrument_keys: Sequence[str], lookback_days: int
    ) -> dict[str, list[DailyBar]]:
        """Concurrent across symbols; each worker thread gets its own DB connection."""
        if not instrument_keys:
            return {}
        results: dict[str, list[DailyBar]] = {}
        workers = min(MAX_CONCURRENT_HISTORY_FETCHES, len(instrument_keys))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.daily, key, lookback_days): key for key in instrument_keys}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results
