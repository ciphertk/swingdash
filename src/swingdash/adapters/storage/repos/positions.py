"""Positions in the Risk tab - entered by hand or imported from a broker - open and closed."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterable

from swingdash.adapters.storage.db import Database
from swingdash.domain.broker import ImportedPosition
from swingdash.domain.risk.portfolio import MANUAL, Position

_COLUMNS = (
    "symbol, instrument_key, quantity, entry, stop, initial_stop, opened_on, funding, note,"
    " closed_on, exit_price, source, broker_ref, planned_quantity, planned_stop, charges"
)


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
        stop: float | None,
        opened_on: dt.date,
        note: str = "",
        *,
        planned: bool = True,
        funding: str = "normal",
    ) -> int:
        """A manual position. `planned`: it was sized first, so its quantity and stop are the plan."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"INSERT INTO positions ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    None,
                    None,
                    MANUAL,
                    None,
                    quantity if planned else None,
                    stop if planned else None,
                    0.0,
                ),
            )
            return int(cursor.lastrowid or 0)

    def update(
        self,
        position_id: int,
        *,
        quantity: int,
        entry: float,
        stop: float | None,
        note: str,
        opened_on: dt.date,
    ) -> None:
        """The initial stop and the plan stay: they're what the trade set out to risk."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE positions SET quantity = ?, entry = ?, stop = ?, note = ?, opened_on = ?,"
                " initial_stop = COALESCE(initial_stop, ?) WHERE id = ?",
                (quantity, entry, stop, note, opened_on.isoformat(), stop, position_id),
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

    # --- imported positions ------------------------------------------------------

    def by_ref(self, source: str) -> dict[str, Position]:
        rows = (
            self._db.connection()
            .execute(
                "SELECT * FROM positions WHERE source = ? AND broker_ref IS NOT NULL", (source,)
            )
            .fetchall()
        )
        return {row["broker_ref"]: _position(row) for row in rows}

    def save_imported(
        self,
        source: str,
        imported: ImportedPosition,
        instrument_key: str | None,
        *,
        into: int | None = None,
    ) -> int:
        """
        Insert or refresh an imported row. The broker owns quantity, prices,
        dates and charges; the user's stop, plan and note are left alone.
        `into`: an existing row (e.g. a manual plan) to turn into this import.
        """
        broker_owned = (
            imported.symbol,
            instrument_key,
            imported.quantity,
            imported.entry,
            imported.opened_on.isoformat(),
            imported.funding,
            imported.closed_on.isoformat() if imported.closed_on else None,
            imported.exit_price,
            source,
            imported.ref,
            imported.charges,
        )
        with self._db.transaction() as conn:
            if into is None:
                row = conn.execute(
                    "SELECT id FROM positions WHERE broker_ref = ?", (imported.ref,)
                ).fetchone()
                into = row["id"] if row else None
            if into is not None:
                conn.execute(
                    "UPDATE positions SET symbol = ?, instrument_key = ?, quantity = ?, entry = ?,"
                    " opened_on = ?, funding = ?, closed_on = ?, exit_price = ?, source = ?,"
                    " broker_ref = ?, charges = ?,"
                    " note = CASE WHEN note = '' THEN ? ELSE note END WHERE id = ?",
                    (*broker_owned, imported.note, into),
                )
                return into
            cursor = conn.execute(
                "INSERT INTO positions (symbol, instrument_key, quantity, entry, opened_on,"
                " funding, closed_on, exit_price, source, broker_ref, charges, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*broker_owned, imported.note),
            )
            return int(cursor.lastrowid or 0)

    def delete_refs(self, source: str, refs: Iterable[str]) -> int:
        refs = list(refs)
        if not refs:
            return 0
        with self._db.transaction() as conn:
            return conn.executemany(
                "DELETE FROM positions WHERE source = ? AND broker_ref = ?",
                [(source, ref) for ref in refs],
            ).rowcount

    def ignore(self, source: str, ref: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO broker_ignored (broker, ref) VALUES (?, ?)", (source, ref)
            )

    def ignored(self, source: str) -> set[str]:
        rows = (
            self._db.connection()
            .execute("SELECT ref FROM broker_ignored WHERE broker = ?", (source,))
            .fetchall()
        )
        return {row["ref"] for row in rows}


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
        source=row["source"],
        broker_ref=row["broker_ref"],
        planned_quantity=row["planned_quantity"],
        planned_stop=row["planned_stop"],
        charges=row["charges"],
    )
