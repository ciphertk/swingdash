"""
Daily candles with a local cache.

A completed trading day's candle never changes, so only candles newer than
the last cached date are fetched - near-real-time tier per CLAUDE.md's
caching guidance, separate from the daily-TTL fundamentals tier.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.domain.bars import DailyBar
from swingdash.services.ports import HistorySource

# One network round trip per symbol (no batch endpoint); capped well under
# Upstox's 50 requests/second limit.
MAX_CONCURRENT_HISTORY_FETCHES = 10


class CandleService:
    def __init__(
        self, history: HistorySource, repo: CandleRepository, today: Callable[[], dt.date]
    ) -> None:
        self._history = history
        self._repo = repo
        self._today = today

    def daily(self, instrument_key: str, lookback_days: int) -> list[DailyBar]:
        """
        Completed daily candles for the lookback window, fetching only what
        the cache lacks - usually a 1-2 day delta, or nothing.

        On a weekend/holiday this still makes one small delta call (which
        comes back empty); not worth calendar logic at this scale.
        """
        today = self._today()
        from_date = today - dt.timedelta(days=lookback_days)

        latest_cached = self._repo.latest_date(instrument_key)
        if latest_cached is None:
            bars = self._history.daily_candles(instrument_key, from_date, today)
            self._repo.upsert(instrument_key, bars)
            return bars

        # History never includes today's open bar, so having yesterday's
        # candle means the cache is fully warm.
        if latest_cached < today - dt.timedelta(days=1):
            delta = self._history.daily_candles(
                instrument_key, latest_cached + dt.timedelta(days=1), today
            )
            self._repo.upsert(instrument_key, delta)

        return self._repo.read_range(instrument_key, from_date, today)

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
