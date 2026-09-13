"""
Data shapes the live engine passes around.

Deliberately UI-free: a UI renders a Snapshot, it never reaches into
engine state. That's what lets more than one UI share one
engine process (which they must - Upstox allows only 2 feed connections
per account, so each UI cannot open its own).
"""

from __future__ import annotations

import datetime as dt
from array import array
from dataclasses import dataclass

# A symbol needs at least this many days of history before its baseline is
# trustworthy; below it the row still renders but is flagged degraded.
MIN_TRUSTWORTHY_DAYS = 5


@dataclass(frozen=True)
class Baseline:
    """
    `curve[m]` = average CUMULATIVE volume by minute-of-session `m`,
    averaged over the last N trading days. Strictly non-decreasing.
    """

    curve: array
    avg_full_day_volume: float
    days_used: int

    @property
    def degraded(self) -> bool:
        return self.days_used < MIN_TRUSTWORTHY_DAYS


@dataclass(frozen=True)
class SymbolRow:
    """One rendered row. Every field is already computed - no UI math."""

    symbol: str
    instrument_key: str
    ltp: float | None
    prev_close: float | None
    change_pct: float | None
    volume: int | None
    rvol: float | None  # time-of-day normalised (the live signal)
    rvol_day: float | None  # raw day-so-far (legacy Pine equivalent)
    classification: str
    degraded: bool
    stale: bool
    flashing: bool


@dataclass(frozen=True)
class Snapshot:
    rows: tuple[SymbolRow, ...]
    minute: int | None  # minute-of-session, None outside the session
    session_date: dt.date | None  # the session these numbers belong to - not always today
    session_minutes: int
    market_status: str  # from the feed's market_info, e.g. NORMAL_OPEN
    ticks_received: int
