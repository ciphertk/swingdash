from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from swingdash.adapters.storage.db import Database
from swingdash.domain.fundamentals import CompanyProfile

# Well under SQLite's bound-parameter limit on any build.
_CHUNK = 500


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

    def read_many(self, isins: Iterable[str]) -> dict[str, tuple[CompanyProfile, dt.datetime]]:
        """Cached profiles for any of `isins`, keyed by ISIN; uncached ones are absent."""
        wanted = list(dict.fromkeys(isins))
        found: dict[str, tuple[CompanyProfile, dt.datetime]] = {}
        conn = self._db.connection()
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start : start + _CHUNK]
            for row in conn.execute(
                "SELECT isin, sector, market_cap_cr, company_profile, fetched_at"
                f" FROM fundamentals_cache WHERE isin IN ({', '.join('?' * len(chunk))})",
                chunk,
            ):
                found[row["isin"]] = (
                    CompanyProfile(row["sector"], row["market_cap_cr"], row["company_profile"]),
                    dt.datetime.fromisoformat(row["fetched_at"]),
                )
        return found

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
