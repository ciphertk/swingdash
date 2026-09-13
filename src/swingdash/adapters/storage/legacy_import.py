"""
One-time import of data from the pre-packaging layout (a repo checkout with
`data/swing_dashboard.db`, instrument JSON caches, and `.env`).

Never deletes or modifies the source.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

LEGACY_DB = Path("data") / "swing_dashboard.db"
LEGACY_EQUITIES = Path("data") / "nse_equity_instruments.json"
LEGACY_INDICES = Path("data") / "nse_index_instruments.json"


@dataclass
class LegacyImportReport:
    database: Path
    backed_up_existing: Path | None = None
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def import_legacy(
    source_root: Path,
    *,
    database: Path,
    equity_instruments: Path,
    index_instruments: Path,
    env_file: Path,
    force: bool = False,
) -> LegacyImportReport:
    legacy_db = source_root / LEGACY_DB
    if not legacy_db.is_file():
        raise FileNotFoundError(f"no legacy database at {legacy_db}")

    report = LegacyImportReport(database=database)
    if database.exists():
        if not force:
            raise FileExistsError(
                f"{database} already exists; re-run with --force to replace it "
                "(the current file is backed up first)"
            )
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        report.backed_up_existing = database.with_name(f"{database.name}.bak-{stamp}")
        shutil.copy2(database, report.backed_up_existing)

    database.parent.mkdir(parents=True, exist_ok=True)
    # The online backup API, not a file copy: it captures a consistent
    # snapshot including anything still in the source's WAL file.
    source = sqlite3.connect(f"file:{legacy_db.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(database)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    report.copied.append(f"{legacy_db} -> {database}")

    for legacy, destination in (
        (source_root / LEGACY_EQUITIES, equity_instruments),
        (source_root / LEGACY_INDICES, index_instruments),
    ):
        if legacy.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, destination)
            report.copied.append(f"{legacy} -> {destination}")
        else:
            report.skipped.append(f"{legacy} (not found)")

    legacy_env = source_root / ".env"
    if legacy_env.is_file() and not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_env, env_file)
        report.copied.append(f"{legacy_env} -> {env_file}")
    elif legacy_env.is_file():
        report.skipped.append(f"{legacy_env} ({env_file} already exists)")

    return report
