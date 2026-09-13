"""
Burst Score - port of Burst Power.pine.

Scans daily bars for big-move-up closing days and weights them into one
score:

    power_score = round(count_5pct * 0.2 + count_10pct * 0.5 + count_19pct * 2)

where count_Npct counts days whose close-over-close move fell in that
bucket (5-10%, 10-19%, 19%+).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from swingdash.domain.bars import DailyBar


@dataclass(frozen=True)
class BurstScoreResult:
    power_score: int
    count_5pct: int
    count_10pct: int
    count_19pct: int
    max_move_pct: float | None
    max_move_date: str | None
    last_date_5pct: str | None
    last_date_10pct: str | None
    last_date_19pct: str | None
    classification: str  # "strong" | "moderate" | "weak"


def compute_burst_score(
    bars: Sequence[DailyBar],
    lookback_days: int | None = None,
    min_close_position: float | None = None,
) -> BurstScoreResult:
    """
    lookback_days: trim to the last N bars before scoring (Pine's
    "Lookback Period" input, default 3 years = ~756 trading days). None
    uses whatever bars are passed in - the caller (ingestion/dashboard
    service) decides how much history to fetch.

    min_close_position: optional filter (0-1) requiring the day's close
    to sit at least this far up its high/low range before the move counts
    - Pine's "closing within % of highs" toggle. None (default) disables
    it, matching the Pine script's own default (toggleclosing=false).
    """
    if lookback_days is not None:
        bars = bars[-lookback_days:]

    count_5 = count_10 = count_19 = 0
    last_date_5: str | None = None
    last_date_10: str | None = None
    last_date_19: str | None = None
    max_move: float | None = None
    max_move_date: str | None = None

    for prev_bar, bar in pairwise(bars):
        if bar.high == bar.low or prev_bar.close == 0:
            continue

        close_position = (bar.close - bar.low) / (bar.high - bar.low)
        if min_close_position is not None and close_position < min_close_position:
            continue

        move = (bar.close - prev_bar.close) / prev_bar.close * 100

        if max_move is None or move > max_move:
            max_move, max_move_date = move, bar.date
        elif move == max_move:
            max_move_date = bar.date

        if 5 <= move < 10:
            count_5 += 1
            last_date_5 = bar.date
        elif 10 <= move < 19:
            count_10 += 1
            last_date_10 = bar.date
        elif move >= 19:
            count_19 += 1
            last_date_19 = bar.date

    power_score = round(count_5 * 0.2 + count_10 * 0.5 + count_19 * 2)

    return BurstScoreResult(
        power_score=power_score,
        count_5pct=count_5,
        count_10pct=count_10,
        count_19pct=count_19,
        max_move_pct=max_move,
        max_move_date=max_move_date,
        last_date_5pct=last_date_5,
        last_date_10pct=last_date_10,
        last_date_19pct=last_date_19,
        classification=classify_burst_score(power_score),
    )


def classify_burst_score(power_score: int) -> str:
    if power_score >= 15:
        return "strong"
    if power_score >= 10:
        return "moderate"
    return "weak"
