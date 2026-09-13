"""
Schema versioning via SQLite's `PRAGMA user_version`.

Append new migrations; never edit a released one. Migration 1 is exactly
the schema the pre-versioning app created with CREATE TABLE IF NOT EXISTS,
so an existing database (user_version 0) is adopted in place without
touching its data.
"""

from __future__ import annotations

from swingdash.adapters.storage.db import Database

_V1_BASELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlists (
    name TEXT PRIMARY KEY,
    symbols_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_candles (
    instrument_key TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (instrument_key, date)
);

CREATE TABLE IF NOT EXISTS fundamentals_cache (
    isin TEXT PRIMARY KEY,
    sector TEXT,
    market_cap_cr REAL,
    company_profile TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_sessions (
    date TEXT PRIMARY KEY,
    open_ms INTEGER,
    close_ms INTEGER,
    is_trading_day INTEGER NOT NULL
);

-- `curve` is a raw array('d') blob (one float per minute of session): an
-- opaque vector that is always read whole, never filtered in SQL.
CREATE TABLE IF NOT EXISTS rvol_baseline (
    instrument_key TEXT NOT NULL,
    session_date TEXT NOT NULL,
    days_used INTEGER NOT NULL,
    avg_full_day_volume REAL NOT NULL,
    curve BLOB NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (instrument_key, session_date)
);
"""

_V2_APP_STATE = """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _V1_BASELINE_SCHEMA),
    (2, _V2_APP_STATE),
)
LATEST_VERSION = MIGRATIONS[-1][0]


class SchemaTooNewError(RuntimeError):
    pass


def schema_version(db: Database) -> int:
    return int(db.connection().execute("PRAGMA user_version").fetchone()[0])


def migrate(db: Database) -> int:
    """Apply pending migrations, each atomically with its version bump."""
    version = schema_version(db)
    if version > LATEST_VERSION:
        raise SchemaTooNewError(
            f"{db.path} is schema v{version}, newer than this swingdash supports "
            f"(v{LATEST_VERSION}). Upgrade swingdash."
        )

    conn = db.connection()
    for number, sql in MIGRATIONS:
        if number <= version:
            continue
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
    return schema_version(db)
