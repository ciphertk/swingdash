"""
Company fundamentals with a daily TTL - CLAUDE.md's second caching tier.
Market cap and sector don't change intraday, so a staleness check is
enough; no delta logic.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable

from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.domain.fundamentals import CompanyProfile
from swingdash.services.ports import FundamentalsSource

logger = logging.getLogger(__name__)

CACHE_TTL = dt.timedelta(hours=24)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class FundamentalsService:
    def __init__(
        self,
        source: FundamentalsSource,
        repo: FundamentalsRepository,
        clock: Callable[[], dt.datetime] = _utc_now,
    ) -> None:
        self._source = source
        self._repo = repo
        self._clock = clock

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
