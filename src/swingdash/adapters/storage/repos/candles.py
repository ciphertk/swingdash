from __future__ import annotations

import datetime as dt

from swingdash.adapters.storage.db import Database
from swingdash.domain.bars import DailyBar


class CandleRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def latest_date(self, instrument_key: str) -> dt.date | None:
        row = (
            self._db.connection()
            .execute(
                "SELECT MAX(date) AS latest FROM daily_candles WHERE instrument_key = ?",
                (instrument_key,),
            )
            .fetchone()
        )
        return dt.date.fromisoformat(row["latest"]) if row and row["latest"] else None

    def read_range(
        self, instrument_key: str, from_date: dt.date, to_date: dt.date
    ) -> list[DailyBar]:
        rows = (
            self._db.connection()
            .execute(
                """
                SELECT date, open, high, low, close, volume FROM daily_candles
                WHERE instrument_key = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
                """,
                (instrument_key, from_date.isoformat(), to_date.isoformat()),
            )
            .fetchall()
        )
        return [
            DailyBar(row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"])
            for row in rows
        ]

    def upsert(self, instrument_key: str, bars: list[DailyBar]) -> None:
        if not bars:
            return
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO daily_candles (instrument_key, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_key, date) DO UPDATE SET
                    open = excluded.open, high = excluded.high, low = excluded.low,
                    close = excluded.close, volume = excluded.volume
                """,
                [(instrument_key, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
            )
