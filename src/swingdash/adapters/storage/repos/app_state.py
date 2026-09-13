"""Small persistent key/value store for app-level UI state (e.g. last watchlist)."""

from __future__ import annotations

from swingdash.adapters.storage.db import Database


class AppStateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> str | None:
        row = (
            self._db.connection()
            .execute("SELECT value FROM app_state WHERE key = ?", (key,))
            .fetchone()
        )
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete(self, key: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
