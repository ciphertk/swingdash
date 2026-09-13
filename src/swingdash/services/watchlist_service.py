"""
Watchlist service - CRUD for saved watchlists (paste/import, multiple
named lists), backed by SQLite. This is the "more than one watchlist"
trigger CLAUDE.md flagged for moving off flat JSON files.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from swingdash.config import WATCHLIST_PATH
from swingdash.db import get_connection, transaction

DEFAULT_WATCHLIST_NAME = "default"


def _seed_default_watchlist_if_missing() -> None:
    """
    One-time migration: the original flat-file default watchlist becomes
    the first row in SQLite, so switching storage didn't change what the
    dashboard shows on a fresh checkout.
    """
    conn = get_connection()
    exists = conn.execute(
        "SELECT 1 FROM watchlists WHERE name = ?", (DEFAULT_WATCHLIST_NAME,)
    ).fetchone()
    if exists or not WATCHLIST_PATH.exists():
        return
    data = json.loads(WATCHLIST_PATH.read_text())
    save_watchlist(DEFAULT_WATCHLIST_NAME, data.get("symbols", []))


def parse_symbols_text(text: str) -> list[str]:
    """
    Paste-import parsing. Accepts commas, newlines, semicolons or plain
    spaces as separators, uppercases, and de-duplicates while preserving
    order.

    Exchange prefixes are stripped: watchlists are usually copied out of
    TradingView or a screener, which write "NSE:RAYMOND", while the
    instrument master is keyed on the bare trading symbol. Keeping the
    prefix silently resolves every symbol to nothing and renders an empty
    table. NSE trading symbols never contain spaces or colons, so this is
    unambiguous.
    """
    seen: set[str] = set()
    symbols: list[str] = []
    for token in re.split(r"[,\s;]+", text):
        symbol = token.strip().upper()
        if ":" in symbol:
            symbol = symbol.rsplit(":", 1)[-1]  # NSE:RAYMOND -> RAYMOND
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def list_watchlists() -> list[dict]:
    _seed_default_watchlist_if_missing()
    rows = (
        get_connection()
        .execute("SELECT name, symbols_json, updated_at FROM watchlists ORDER BY name")
        .fetchall()
    )
    return [
        {
            "name": row["name"],
            "symbols": json.loads(row["symbols_json"]),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_watchlist(name: str) -> list[str] | None:
    _seed_default_watchlist_if_missing()
    row = (
        get_connection()
        .execute("SELECT symbols_json FROM watchlists WHERE name = ?", (name,))
        .fetchone()
    )
    return json.loads(row["symbols_json"]) if row else None


def save_watchlist(name: str, symbols: list[str]) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO watchlists (name, symbols_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                symbols_json = excluded.symbols_json, updated_at = excluded.updated_at
            """,
            (name, json.dumps(symbols), dt.datetime.now(dt.UTC).isoformat()),
        )


def delete_watchlist(name: str) -> bool:
    with transaction() as conn:
        cursor = conn.execute("DELETE FROM watchlists WHERE name = ?", (name,))
        return cursor.rowcount > 0


def load_default_watchlist() -> list[str]:
    return get_watchlist(DEFAULT_WATCHLIST_NAME) or []
