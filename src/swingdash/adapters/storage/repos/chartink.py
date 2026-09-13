"""Saved Chartink screeners and widgets, each with its last result."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from swingdash.adapters.storage.db import Database
from swingdash.domain.chartink import (
    ChartinkItem,
    ChartinkKind,
    ChartinkRequest,
    ChartinkResult,
    ChartinkRow,
)

# (name, request, source_url, collection)
NewItem = tuple[str, ChartinkRequest, str | None, str | None]


class ChartinkRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def all(self) -> list[ChartinkItem]:
        rows = (
            self._db.connection()
            .execute(
                "SELECT * FROM chartink_items"
                " ORDER BY collection IS NOT NULL, collection, position, id"
            )
            .fetchall()
        )
        return [_item(row) for row in rows]

    def get(self, item_id: int) -> ChartinkItem | None:
        row = (
            self._db.connection()
            .execute("SELECT * FROM chartink_items WHERE id = ?", (item_id,))
            .fetchone()
        )
        return _item(row) if row else None

    def add(
        self,
        name: str,
        request: ChartinkRequest,
        source_url: str | None = None,
        collection: str | None = None,
    ) -> int:
        return self.add_many([(name, request, source_url, collection)])[0]

    def add_many(self, items: Sequence[NewItem]) -> list[int]:
        """One transaction, so a dashboard import lands whole or not at all."""
        ids: list[int] = []
        now = dt.datetime.now(dt.UTC).isoformat()
        with self._db.transaction() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM chartink_items"
            ).fetchone()[0]
            for name, request, source_url, collection in items:
                position += 1
                cursor = conn.execute(
                    """
                    INSERT INTO chartink_items
                        (name, kind, fields_json, source_url, collection, position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        request.kind.value,
                        json.dumps(request.fields),
                        source_url,
                        collection,
                        position,
                        now,
                    ),
                )
                ids.append(int(cursor.lastrowid or 0))
        return ids

    def rename(self, item_id: int, name: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE chartink_items SET name = ? WHERE id = ?", (name, item_id))

    def rename_collection(self, old: str, new: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE chartink_items SET collection = ? WHERE collection = ?", (new, old)
            )

    def delete(self, item_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM chartink_items WHERE id = ?", (item_id,))

    def delete_collection(self, collection: str) -> int:
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM chartink_items WHERE collection = ?", (collection,)
            ).rowcount

    def save_result(self, item_id: int, result: ChartinkResult, fetched_at: dt.datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE chartink_items SET result_json = ?, fetched_at = ?, error = NULL"
                " WHERE id = ?",
                (json.dumps(_result_to_json(result)), fetched_at.isoformat(), item_id),
            )

    def save_error(self, item_id: int, error: str) -> None:
        """Record a failed run, keeping the last good result."""
        with self._db.transaction() as conn:
            conn.execute("UPDATE chartink_items SET error = ? WHERE id = ?", (error, item_id))


def _item(row: sqlite3.Row) -> ChartinkItem:
    return ChartinkItem(
        id=row["id"],
        name=row["name"],
        request=ChartinkRequest(ChartinkKind(row["kind"]), json.loads(row["fields_json"])),
        source_url=row["source_url"],
        collection=row["collection"],
        result=_result_from_json(json.loads(row["result_json"])) if row["result_json"] else None,
        fetched_at=dt.datetime.fromisoformat(row["fetched_at"]) if row["fetched_at"] else None,
        error=row["error"],
    )


def _result_to_json(result: ChartinkResult) -> dict[str, Any]:
    return {
        "columns": list(result.columns),
        "rows": [[row.key, row.values] for row in result.rows],
        "group_by": result.group_by,
        "data_time": result.data_time.isoformat() if result.data_time else None,
        "available": result.available,
    }


def _result_from_json(data: dict[str, Any]) -> ChartinkResult:
    return ChartinkResult(
        columns=tuple(data["columns"]),
        rows=tuple(ChartinkRow(key, values) for key, values in data["rows"]),
        group_by=data.get("group_by"),
        data_time=dt.datetime.fromisoformat(data["data_time"]) if data.get("data_time") else None,
        available=data.get("available"),
    )
