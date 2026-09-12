"""
SQLite connection - one file, shared by the candle cache
(services/candle_cache_service.py) and saved watchlists
(services/watchlist_service.py). Personal single-user tool at this
scale (CLAUDE.md's scalability notes) - SQLite is enough, no need for a
client/server DB or an ORM.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from app.config import DB_PATH

_SCHEMA = """
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

-- Per-symbol cumulative-volume-by-minute baseline for intraday RVOL.
-- `curve` is a raw array('d') blob (one float per minute of session)
-- rather than 375 rows per symbol: it's an opaque vector that is always
-- read whole and never filtered by minute in SQL.
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

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """
    One connection per thread, not one shared connection for the whole
    process. A single sqlite3.Connection object is not safe to use from
    more than one thread at a time - even with check_same_thread=False,
    which only disables Python's own guard rail, not the underlying
    C library's thread-affinity - and candle_cache_service reads/writes
    from a thread pool, so a shared connection would intermittently
    corrupt in-flight statements ("bad parameter or other API misuse").
    WAL mode + a busy timeout let SQLite's own file-level locking queue
    the rare concurrent write instead of raising immediately.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
