"""Company profile (Upstox Fundamentals API, keyed by ISIN)."""

from __future__ import annotations

from typing import Any

from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.domain.fundamentals import CompanyProfile


class UpstoxFundamentals:
    def __init__(self, client: UpstoxClient) -> None:
        self._client = client

    def company_profile(self, isin: str) -> CompanyProfile:
        """
        `sector_market_cap_inr`, despite its name, is the company's own market
        cap - confirmed by comparing two companies in the same sector
        (HDFCBANK, ICICIBANK) and getting two different plausible values.
        """
        response: Any = self._client.fundamentals().get_company_profile(isin)
        data = response.data
        return CompanyProfile(
            sector=data.sector,
            market_cap_cr=data.sector_market_cap_inr.value if data.sector_market_cap_inr else None,
            company_profile=data.company_profile,
        )
