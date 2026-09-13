from __future__ import annotations

import datetime as dt

from swingdash.adapters.storage.db import Database
from swingdash.domain.calendar import IST, Session


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def read(self, date: dt.date) -> tuple[bool, Session | None]:
        """
        Returns (found, session). The flag matters: a cached non-trading day
        and a date never looked up both have no Session, and conflating them
        meant non-trading days were never actually served from cache.
        """
        row = (
            self._db.connection()
            .execute(
                "SELECT open_ms, close_ms, is_trading_day FROM market_sessions WHERE date = ?",
                (date.isoformat(),),
            )
            .fetchone()
        )
        if row is None:
            return False, None
        if not row["is_trading_day"]:
            return True, None
        return True, Session(
            date=date,
            open_at=dt.datetime.fromtimestamp(row["open_ms"] / 1000, IST),
            close_at=dt.datetime.fromtimestamp(row["close_ms"] / 1000, IST),
        )

    def write(self, date: dt.date, bounds: tuple[dt.datetime, dt.datetime] | None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO market_sessions (date, open_ms, close_ms, is_trading_day)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    open_ms = excluded.open_ms, close_ms = excluded.close_ms,
                    is_trading_day = excluded.is_trading_day
                """,
                (
                    date.isoformat(),
                    int(bounds[0].timestamp() * 1000) if bounds else None,
                    int(bounds[1].timestamp() * 1000) if bounds else None,
                    1 if bounds else 0,
                ),
            )
