import sqlite3
from pathlib import Path

import pytest

from swingdash.adapters.storage.legacy_import import import_legacy


@pytest.fixture
def legacy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "old-checkout"
    (root / "data").mkdir(parents=True)
    conn = sqlite3.connect(root / "data" / "swing_dashboard.db")
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE watchlists (name TEXT PRIMARY KEY, symbols_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO watchlists VALUES ('W01', '["SBIN"]', 'x'), ('nxtDay', '["RAYMOND"]', 'x');
        """
    )
    conn.close()
    (root / "data" / "nse_equity_instruments.json").write_text("[]")
    (root / "data" / "nse_index_instruments.json").write_text("[]")
    (root / ".env").write_text("UPSTOX_ANALYTICS_TOKEN=abc\n")
    return root


def _import(root: Path, target: Path, force: bool = False):
    return import_legacy(
        root,
        database=target / "swingdash.db",
        equity_instruments=target / "cache" / "eq.json",
        index_instruments=target / "cache" / "idx.json",
        env_file=target / "config" / ".env",
        force=force,
    )


def test_copies_database_caches_and_env_without_touching_source(legacy_repo: Path, tmp_path: Path):
    source_db = legacy_repo / "data" / "swing_dashboard.db"
    before = source_db.read_bytes()
    target = tmp_path / "new-home"

    _import(legacy_repo, target)

    conn = sqlite3.connect(target / "swingdash.db")
    names = [r[0] for r in conn.execute("SELECT name FROM watchlists ORDER BY name")]
    conn.close()
    assert names == ["W01", "nxtDay"]
    assert (target / "cache" / "eq.json").is_file()
    assert (target / "config" / ".env").is_file()
    assert source_db.read_bytes() == before


def test_refuses_to_overwrite_without_force_and_backs_up_with_it(legacy_repo: Path, tmp_path: Path):
    target = tmp_path / "new-home"
    _import(legacy_repo, target)

    with pytest.raises(FileExistsError):
        _import(legacy_repo, target)

    report = _import(legacy_repo, target, force=True)
    assert report.backed_up_existing is not None
    assert report.backed_up_existing.is_file()


def test_missing_legacy_database_is_reported(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _import(tmp_path / "nowhere", tmp_path / "new-home")
