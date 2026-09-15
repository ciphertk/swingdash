"""Positions entered in the Risk tab, open and closed."""

from __future__ import annotations

import datetime as dt
import sqlite3

from swingdash.adapters.storage.db import Database
from swingdash.domain.risk.portfolio import Position


class PositionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def all(self) -> list[Position]:
        """Open positions first (oldest first), then closed ones (latest first)."""
        rows = (
            self._db.connection()
            .execute(
                "SELECT * FROM positions ORDER BY closed_on IS NOT NULL, "
                "CASE WHEN closed_on IS NULL THEN opened_on END, closed_on DESC, id"
            )
            .fetchall()
        )
        return [_position(row) for row in rows]

    def get(self, position_id: int) -> Position | None:
        row = (
            self._db.connection()
            .execute("SELECT * FROM positions WHERE id = ?", (position_id,))
            .fetchone()
        )
        return _position(row) if row else None

    def add(
        self,
        symbol: str,
        instrument_key: str | None,
        quantity: int,
        entry: float,
        stop: float,
        opened_on: dt.date,
        note: str = "",
        funding: str = "normal",
    ) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO positions
                    (symbol, instrument_key, quantity, entry, stop, initial_stop,
                     opened_on, funding, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    instrument_key,
                    quantity,
                    entry,
                    stop,
                    stop,
                    opened_on.isoformat(),
                    funding,
                    note,
                ),
            )
            return int(cursor.lastrowid or 0)

    def update(
        self,
        position_id: int,
        *,
        quantity: int,
        entry: float,
        stop: float,
        note: str,
        opened_on: dt.date,
    ) -> None:
        """The initial stop stays: it's what the trade risked when it was taken."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE positions SET quantity = ?, entry = ?, stop = ?, note = ?, opened_on = ?"
                " WHERE id = ?",
                (quantity, entry, stop, note, opened_on.isoformat(), position_id),
            )

    def close(self, position_id: int, exit_price: float, closed_on: dt.date) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE positions SET exit_price = ?, closed_on = ? WHERE id = ?",
                (exit_price, closed_on.isoformat(), position_id),
            )

    def delete(self, position_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))


def _position(row: sqlite3.Row) -> Position:
    return Position(
        id=row["id"],
        symbol=row["symbol"],
        instrument_key=row["instrument_key"],
        quantity=row["quantity"],
        entry=row["entry"],
        stop=row["stop"],
        initial_stop=row["initial_stop"],
        opened_on=dt.date.fromisoformat(row["opened_on"]),
        funding=row["funding"],
        note=row["note"],
        closed_on=dt.date.fromisoformat(row["closed_on"]) if row["closed_on"] else None,
        exit_price=row["exit_price"],
    )
