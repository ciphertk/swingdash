import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.migrations import (
    LATEST_VERSION,
    SchemaTooNewError,
    migrate,
    schema_version,
)


def test_concurrent_reads_and_writes_from_a_thread_pool(db: Database):
    """A single shared connection crashed under the baseline thread pool
    ("bad parameter or other API misuse"); per-thread connections must not."""

    def work(i: int) -> int:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"k{i % 20}", str(i)),
            )
        return int(db.connection().execute("SELECT COUNT(*) FROM app_state").fetchone()[0])

    with ThreadPoolExecutor(max_workers=16) as pool:
        counts = list(pool.map(work, range(400)))

    assert max(counts) == 20


def test_fresh_database_migrates_to_latest(db: Database):
    assert schema_version(db) == LATEST_VERSION
    tables = {
        r[0] for r in db.connection().execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"watchlists", "rvol_baseline", "market_sessions", "app_state"} <= tables


def test_migrate_is_idempotent(db: Database):
    assert migrate(db) == LATEST_VERSION
    assert migrate(db) == LATEST_VERSION


def test_pre_versioning_database_is_adopted_without_data_loss(tmp_path: Path):
    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE watchlists (name TEXT PRIMARY KEY, symbols_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO watchlists VALUES ('nxtDay', '["RAYMOND"]', '2026-09-11');
        """
    )
    conn.close()

    legacy = Database(legacy_path)
    try:
        assert schema_version(legacy) == 0
        assert migrate(legacy) == LATEST_VERSION
        row = legacy.connection().execute("SELECT symbols_json FROM watchlists").fetchone()
        assert row[0] == '["RAYMOND"]'
    finally:
        legacy.close()


def test_refuses_a_database_from_a_newer_version(db: Database):
    db.connection().execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    with pytest.raises(SchemaTooNewError):
        migrate(db)
