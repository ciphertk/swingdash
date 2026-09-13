"""
CSV export shared by every tab: plain data in, a timestamped file on disk out.

Tabs never write files themselves - they hand `TabBase.action_export_csv`
an `ExportTable` already filtered and sorted exactly as shown on screen.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportTable:
    """One tab's (or subtab's) current view, ready to write out."""

    name: str  # goes into the filename, e.g. "live-rvol", "securities-stocks"
    headers: Sequence[str]
    rows: Sequence[Sequence[object]]


_UNSAFE_FOR_A_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


def write_csv(
    table: ExportTable,
    directory: Path,
    clock: Callable[[], dt.datetime] = dt.datetime.now,
) -> Path:
    """
    Write `table` to a timestamped CSV under `directory` (created if needed).

    `utf-8-sig` so Excel on Windows - the target platform - detects the
    encoding correctly instead of mojibake-ing anything outside ASCII
    (company names, symbols).
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = clock().strftime("%Y-%m-%d_%H%M%S")
    safe_name = _UNSAFE_FOR_A_FILENAME.sub("-", table.name).strip("-") or "export"
    path = directory / f"{safe_name}_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.headers)
        writer.writerows(_plain(row) for row in table.rows)
    return path


def _plain(row: Sequence[object]) -> list[object]:
    """None becomes an empty cell rather than the literal text "None"."""
    return ["" if value is None else value for value in row]
