"""Company profile (Upstox Fundamentals API, keyed by ISIN)."""

from __future__ import annotations

from typing import Any

from upstox_client.rest import ApiException

from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.domain.errors import RateLimitedError
from swingdash.domain.fundamentals import CompanyProfile

_TOO_MANY_REQUESTS = 429


class UpstoxFundamentals:
    def __init__(self, client: UpstoxClient) -> None:
        self._client = client

    def company_profile(self, isin: str) -> CompanyProfile:
        """
        `sector_market_cap_inr`, despite its name, is the company's own market
        cap - confirmed by comparing two companies in the same sector
        (HDFCBANK, ICICIBANK) and getting two different plausible values.
        An ISIN with no fundamentals comes back empty (sector None, cap 0).
        """
        try:
            response: Any = self._client.fundamentals().get_company_profile(isin)
        except ApiException as exc:
            if exc.status == _TOO_MANY_REQUESTS:
                raise RateLimitedError("Upstox fundamentals rate limit") from exc
            raise
        data = response.data
        market_cap = data.sector_market_cap_inr.value if data.sector_market_cap_inr else None
        return CompanyProfile(
            sector=data.sector,
            market_cap_cr=market_cap or None,
            company_profile=data.company_profile,
        )
