"""Company fundamentals model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyProfile:
    sector: str | None
    market_cap_cr: float | None
    company_profile: str | None
