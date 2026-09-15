from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.migrations import migrate


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never let a test read or write the real user data directories or token."""
    home = tmp_path / "swingdash-home"
    monkeypatch.setenv("SWINGDASH_HOME", str(home))
    for name in ("UPSTOX_ANALYTICS_TOKEN", "DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.db")
    migrate(database)
    yield database
    database.close()
