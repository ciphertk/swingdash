from __future__ import annotations

import datetime as dt
import json

from swingdash.adapters.storage.db import Database
from swingdash.domain.watchlist import Watchlist


class WatchlistRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def all(self) -> list[Watchlist]:
        rows = (
            self._db.connection()
            .execute("SELECT name, symbols_json FROM watchlists ORDER BY name")
            .fetchall()
        )
        return [Watchlist(row["name"], tuple(json.loads(row["symbols_json"]))) for row in rows]

    def get(self, name: str) -> Watchlist | None:
        row = (
            self._db.connection()
            .execute("SELECT name, symbols_json FROM watchlists WHERE name = ?", (name,))
            .fetchone()
        )
        return Watchlist(row["name"], tuple(json.loads(row["symbols_json"]))) if row else None

    def count(self) -> int:
        return int(self._db.connection().execute("SELECT COUNT(*) FROM watchlists").fetchone()[0])

    def save(self, watchlist: Watchlist, replacing: str | None = None) -> None:
        """
        Insert or update `watchlist`. With `replacing` (a rename), the old row
        goes in the same transaction, so a crash can't leave both behind.
        """
        with self._db.transaction() as conn:
            if replacing is not None and replacing != watchlist.name:
                conn.execute("DELETE FROM watchlists WHERE name = ?", (replacing,))
            conn.execute(
                """
                INSERT INTO watchlists (name, symbols_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    symbols_json = excluded.symbols_json, updated_at = excluded.updated_at
                """,
                (
                    watchlist.name,
                    json.dumps(list(watchlist.symbols)),
                    dt.datetime.now(dt.UTC).isoformat(),
                ),
            )

    def delete(self, name: str) -> bool:
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM watchlists WHERE name = ?", (name,)).rowcount > 0
