from __future__ import annotations

import datetime as dt

from swingdash.adapters.storage.db import Database
from swingdash.domain.fundamentals import CompanyProfile


class FundamentalsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def read(self, isin: str) -> tuple[CompanyProfile, dt.datetime] | None:
        """The cached profile and when it was fetched, or None."""
        row = (
            self._db.connection()
            .execute(
                "SELECT sector, market_cap_cr, company_profile, fetched_at"
                " FROM fundamentals_cache WHERE isin = ?",
                (isin,),
            )
            .fetchone()
        )
        if not row:
            return None
        profile = CompanyProfile(row["sector"], row["market_cap_cr"], row["company_profile"])
        return profile, dt.datetime.fromisoformat(row["fetched_at"])

    def upsert(self, isin: str, profile: CompanyProfile, fetched_at: dt.datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO fundamentals_cache
                    (isin, sector, market_cap_cr, company_profile, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(isin) DO UPDATE SET
                    sector = excluded.sector, market_cap_cr = excluded.market_cap_cr,
                    company_profile = excluded.company_profile, fetched_at = excluded.fetched_at
                """,
                (
                    isin,
                    profile.sector,
                    profile.market_cap_cr,
                    profile.company_profile,
                    fetched_at.isoformat(),
                ),
            )
