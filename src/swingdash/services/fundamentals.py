"""
Company fundamentals with a daily TTL - CLAUDE.md's second caching tier.
Market cap and sector don't change intraday, so a staleness check is
enough; no delta logic.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.domain.errors import RateLimitedError
from swingdash.domain.fundamentals import CompanyProfile
from swingdash.services.ports import FundamentalsSource

logger = logging.getLogger(__name__)

CACHE_TTL = dt.timedelta(hours=24)

# Sector and market-cap bucket barely move; the bulk backfill refreshes
# monthly rather than inheriting the 24h TTL (which would mean thousands of
# calls a day).
BACKFILL_MAX_AGE = dt.timedelta(days=30)
# ~1,500 calls per 30 minutes: under Upstox's 2,000, leaving headroom for
# RVOL baselines drawing on the same budget.
BACKFILL_MIN_INTERVAL = 1.2
RATE_LIMIT_PAUSE = 60.0
GIVE_UP_AFTER_FAILURES = 20


@dataclass
class BackfillResult:
    total: int
    fetched: int = 0
    failed: int = 0
    cancelled: bool = False
    gave_up: bool = False


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class FundamentalsService:
    def __init__(
        self,
        source: FundamentalsSource,
        repo: FundamentalsRepository,
        clock: Callable[[], dt.datetime] = _utc_now,
        backfill_interval: float = BACKFILL_MIN_INTERVAL,
    ) -> None:
        self._source = source
        self._repo = repo
        self._clock = clock
        self._backfill_interval = backfill_interval

    def company_profile(self, isin: str) -> CompanyProfile | None:
        """
        None if the profile can't be fetched (e.g. no fundamentals for this
        ISIN) rather than raising - a missing profile shouldn't break a view.
        """
        cached = self._repo.read(isin)
        if cached is not None:
            profile, fetched_at = cached
            if self._clock() - fetched_at <= CACHE_TTL:
                return profile

        try:
            profile = self._source.company_profile(isin)
        except Exception:
            logger.warning("company profile unavailable for %s", isin, exc_info=True)
            return None

        self._repo.upsert(isin, profile, self._clock())
        return profile

    def cached_profiles(self, isins: Iterable[str]) -> dict[str, CompanyProfile]:
        """Whatever is cached for `isins`, however old - for display, never fetches."""
        return {isin: profile for isin, (profile, _) in self._repo.read_many(isins).items()}

    def backfill(
        self,
        isins: Sequence[str],
        *,
        cancel: threading.Event,
        max_age: dt.timedelta = BACKFILL_MAX_AGE,
        min_interval: float | None = None,
        rate_limit_pause: float = RATE_LIMIT_PAUSE,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BackfillResult:
        """
        Fetch profiles for every ISIN not cached within `max_age`, one call
        at a time and at most one per `min_interval` seconds (default: the
        service's `backfill_interval`).

        The pacing is the point: Upstox allows 2,000 calls per 30 minutes,
        shared with RVOL's candle history, so a first fill of a few thousand
        ISINs must trickle. Each profile is saved as it arrives - including
        empty ones, so an ISIN without fundamentals isn't retried every time -
        which makes an interrupted backfill resume where it stopped.
        """
        interval = self._backfill_interval if min_interval is None else min_interval
        now = self._clock()
        cached = self._repo.read_many(isins)
        due = [
            isin
            for isin in dict.fromkeys(isins)
            if isin not in cached or now - cached[isin][1] > max_age
        ]
        result = BackfillResult(total=len(due))
        if on_progress:
            on_progress(0, result.total)

        consecutive_failures = 0
        for isin in due:
            while not cancel.is_set():
                started = time.monotonic()
                try:
                    profile = self._source.company_profile(isin)
                except RateLimitedError:
                    logger.info("fundamentals rate limited, pausing %.0fs", rate_limit_pause)
                    cancel.wait(rate_limit_pause)
                    continue
                except Exception:
                    logger.warning("company profile unavailable for %s", isin, exc_info=True)
                    result.failed += 1
                    consecutive_failures += 1
                else:
                    self._repo.upsert(isin, profile, self._clock())
                    result.fetched += 1
                    consecutive_failures = 0
                cancel.wait(max(0.0, interval - (time.monotonic() - started)))
                break
            if cancel.is_set():
                result.cancelled = True
                break
            if on_progress:
                on_progress(result.fetched + result.failed, result.total)
            if consecutive_failures >= GIVE_UP_AFTER_FAILURES:
                # Offline or locked out - don't grind through thousands of doomed calls.
                result.gave_up = True
                break
        return result
