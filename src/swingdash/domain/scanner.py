"""
The Scanner's per-symbol metrics, split into two phases so live updates
cost almost nothing:

- `prepare_symbol` walks a symbol's completed daily history once per
  session (O(bars), ~1-2 ms) - Burst Power in full, Mswing's context.
- `live_symbol` combines that with the current price (O(1)) whenever the
  screen redraws.

Adding a metric: give SymbolContext and SymbolMetrics a field, compute it
in both functions, and add its columns to the Scanner tab.

"Today" follows the same rule as Live RVOL - the last session that has
opened, never the wall clock. Today's bar only exists when the history
ends at the session before it (so nothing is skipped or double counted):
Mswing uses the live price as today's close at any time; Burst Power
counts today only once the session has closed.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.burst_score import (
    BurstScoreResult,
    compute_burst_score,
    extend_burst_score,
    lookback_cutoff,
)
from swingdash.domain.metrics.mswing import (
    MswingContext,
    MswingValue,
    classify_mswing,
    live_mswing,
    prepare_mswing,
)


@dataclass(frozen=True)
class SymbolContext:
    """A symbol's completed history, digested."""

    history_through: dt.date | None
    last_close: float | None
    previous_close: float | None
    burst: BurstScoreResult
    mswing: MswingContext


def prepare_symbol(bars: Sequence[DailyBar], today: dt.date) -> SymbolContext:
    """`bars`: completed daily bars, oldest first. `today`: the active session's date."""
    return SymbolContext(
        history_through=dt.date.fromisoformat(bars[-1].date) if bars else None,
        last_close=bars[-1].close if bars else None,
        previous_close=bars[-2].close if len(bars) >= 2 else None,
        burst=compute_burst_score(bars, since=lookback_cutoff(today)),
        mswing=prepare_mswing(bars),
    )


@dataclass(frozen=True)
class TodayBar:
    """What the engine knows about the active session for one symbol."""

    date: dt.date
    # The session before `date`: history must reach it for today to count.
    previous_session: dt.date | None
    ltp: float | None
    closed: bool  # the session has ended, so its bar is complete


@dataclass(frozen=True)
class SymbolMetrics:
    burst: BurstScoreResult
    mswing: MswingValue
    # True when today's price is part of the numbers; False = as of history_through.
    includes_today: bool
    change_pct: float | None


def live_symbol(ctx: SymbolContext, today: TodayBar) -> SymbolMetrics:
    includes_today = (
        today.ltp is not None
        and ctx.history_through is not None
        and ctx.history_through < today.date
        and (today.previous_session is None or ctx.history_through >= today.previous_session)
    )
    if not includes_today or today.ltp is None or ctx.last_close is None:
        # As of the last completed bar - its own change, e.g. on a weekend.
        change = _change(ctx.last_close, ctx.previous_close)
        return SymbolMetrics(ctx.burst, ctx.mswing.last, False, change)

    burst = ctx.burst
    if today.closed:
        # High/low aren't known from the feed; NaN skips only the
        # close-in-range filter, which is off by default.
        bar = DailyBar(today.date.isoformat(), math.nan, math.nan, math.nan, today.ltp, 0.0)
        burst = extend_burst_score(burst, ctx.last_close, bar)
    return SymbolMetrics(
        burst, live_mswing(ctx.mswing, today.ltp), True, _change(today.ltp, ctx.last_close)
    )


def _change(close: float | None, previous: float | None) -> float | None:
    if close is None or not previous:
        return None
    return (close - previous) / previous * 100


def mswing_class(stock: MswingValue, index: MswingValue) -> str | None:
    if stock.score is None or index.score is None:
        return None
    return classify_mswing(stock.score, index.score)


# --- what the Scanner tab renders ---------------------------------------------


@dataclass(frozen=True)
class ScannerRow:
    """One rendered row; every field already computed."""

    symbol: str
    instrument_key: str
    ltp: float | None
    change_pct: float | None
    # None until the symbol's history has loaded.
    metrics: SymbolMetrics | None
    history_through: dt.date | None
    mswing_class: str | None  # strong | neutral | weak, against the index
    vs_index: float | None  # stock Mswing minus index Mswing


@dataclass(frozen=True)
class ScannerIndex:
    instrument_key: str
    name: str
    metrics: SymbolMetrics | None


@dataclass(frozen=True)
class ScannerSnapshot:
    rows: tuple[ScannerRow, ...]
    index: ScannerIndex
    session_date: dt.date | None
    session_closed: bool
    loaded: int  # symbols with history prepared
    total: int
    fetching: bool  # history deltas still downloading
    market_status: str
