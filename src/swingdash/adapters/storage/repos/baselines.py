"""
RVOL baselines, keyed by session date so a same-day restart is a pure read
and the row goes stale on its own the next session.
"""

from __future__ import annotations

import datetime as dt
from array import array

from swingdash.adapters.storage.db import Database
from swingdash.domain.rvol.types import Baseline


class BaselineRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def load_many(self, instrument_keys: list[str], session_date: dt.date) -> dict[str, Baseline]:
        if not instrument_keys:
            return {}

        placeholders = ",".join("?" * len(instrument_keys))
        rows = (
            self._db.connection()
            .execute(
                f"""
                SELECT instrument_key, days_used, avg_full_day_volume, curve
                FROM rvol_baseline
                WHERE session_date = ? AND instrument_key IN ({placeholders})
                """,
                (session_date.isoformat(), *instrument_keys),
            )
            .fetchall()
        )

        out: dict[str, Baseline] = {}
        for row in rows:
            curve = array("d")
            curve.frombytes(row["curve"])
            out[row["instrument_key"]] = Baseline(
                curve=curve,
                avg_full_day_volume=row["avg_full_day_volume"],
                days_used=row["days_used"],
            )
        return out

    def save(self, instrument_key: str, session_date: dt.date, baseline: Baseline) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO rvol_baseline
                    (instrument_key, session_date, days_used, avg_full_day_volume, curve, built_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_key, session_date) DO UPDATE SET
                    days_used = excluded.days_used,
                    avg_full_day_volume = excluded.avg_full_day_volume,
                    curve = excluded.curve,
                    built_at = excluded.built_at
                """,
                (
                    instrument_key,
                    session_date.isoformat(),
                    baseline.days_used,
                    baseline.avg_full_day_volume,
                    baseline.curve.tobytes(),
                    dt.datetime.now(dt.UTC).isoformat(),
                ),
            )
