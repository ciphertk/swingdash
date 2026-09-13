"""
Shared data shapes passed between ingestion and the engines below.

One canonical daily bar shape so every engine takes the same input type -
that's what lets a caller (the TUI, or a future backtest script) feed one
list of bars to many metric functions without a per-metric adapter.
"""

from __future__ import annotations

from typing import NamedTuple


class DailyBar(NamedTuple):
    date: str  # ISO date string (yyyy-mm-dd) - engines never parse it, just pass it through
    open: float
    high: float
    low: float
    close: float
    volume: float


class MinuteBar(NamedTuple):
    """
    One intraday candle. `ts` keeps its full ISO8601 offset (+05:30) rather
    than a bare date, because minute-of-session is derived from the clock
    time - see app/live/session.py.
    """

    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
