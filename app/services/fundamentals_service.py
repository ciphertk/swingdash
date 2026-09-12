"""
Fundamentals - company profile (market cap, sector), cached locally with
a daily TTL. This is CLAUDE.md's second explicit caching tier: unlike
candle_cache_service (near-real-time, delta-fetched), fundamentals don't
change intraday, so a simple "is this older than a day?" check is enough
- no need for the candle cache's delta-fetch logic here.
"""
from __future__ import annotations

import datetime as dt

from app.db import get_connection, transaction
from app.services import ingestion_service

CACHE_TTL_HOURS = 24


def _read_cached(isin: str) -> dict | None:
    row = get_connection().execute(
        "SELECT sector, market_cap_cr, company_profile, fetched_at FROM fundamentals_cache WHERE isin = ?",
        (isin,),
    ).fetchone()
    if not row:
        return None

    fetched_at = dt.datetime.fromisoformat(row["fetched_at"])
    if dt.datetime.now(dt.timezone.utc) - fetched_at > dt.timedelta(hours=CACHE_TTL_HOURS):
        return None

    return {
        "sector": row["sector"],
        "market_cap_cr": row["market_cap_cr"],
        "company_profile": row["company_profile"],
    }


def _upsert_cache(isin: str, profile: dict) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO fundamentals_cache (isin, sector, market_cap_cr, company_profile, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(isin) DO UPDATE SET
                sector = excluded.sector, market_cap_cr = excluded.market_cap_cr,
                company_profile = excluded.company_profile, fetched_at = excluded.fetched_at
            """,
            (
                isin,
                profile["sector"],
                profile["market_cap_cr"],
                profile["company_profile"],
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )


def get_company_profile_cached(isin: str) -> dict | None:
    """
    Returns None if the profile can't be fetched (e.g. Upstox has no
    fundamentals for this ISIN) rather than raising - a missing profile
    shouldn't take down the rest of a stock's detail page.
    """
    cached = _read_cached(isin)
    if cached is not None:
        return cached

    try:
        profile = ingestion_service.fetch_company_profile(isin)
    except Exception:
        return None

    _upsert_cache(isin, profile)
    return profile
