"""
Burst Score (Burst Power) - port of Burst Power.pine.

Scans daily bars for big-move-up closing days and weights them into one
score:

    power_score = round(count_5pct / 5 + count_10pct / 2 + count_19pct / 0.5)

where count_Npct counts days whose close-over-close move fell in that
bucket (5-10%, 10-19%, 19%+).

The score is a fold over (previous close, bar) pairs, so a result can be
extended by one more day in O(1) - that's how a completed session is added
without rescanning three years of history.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from swingdash.domain.bars import DailyBar

DEFAULT_LOOKBACK_YEARS = 3
STRONG_POWER = 15
MODERATE_POWER = 10


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


EMPTY_BURST = BurstScoreResult(0, 0, 0, 0, None, None, None, None, None, "weak")


def lookback_cutoff(today: dt.date, years: int = DEFAULT_LOOKBACK_YEARS) -> dt.date:
    """
    Pine's cutoff: the same month and day, `years` earlier. A 29 Feb that
    doesn't exist rolls over to 1 Mar, as Pine's timestamp() does.
    """
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return dt.date(today.year - years, 3, 1)


class _Tally:
    """The running counts. Mutable so a 3-year scan allocates one result, not 750."""

    __slots__ = ("c5", "c10", "c19", "d5", "d10", "d19", "max_date", "max_move")

    def __init__(self, result: BurstScoreResult) -> None:
        self.c5, self.c10, self.c19 = result.count_5pct, result.count_10pct, result.count_19pct
        self.d5, self.d10, self.d19 = (
            result.last_date_5pct,
            result.last_date_10pct,
            result.last_date_19pct,
        )
        self.max_move, self.max_date = result.max_move_pct, result.max_move_date

    def add(self, prev_close: float, bar: DailyBar, min_close_position: float | None) -> None:
        """
        A bar whose high or low is unknown (NaN, e.g. a day built from the
        live feed's last price) skips only the close-position filter.
        """
        if prev_close == 0:
            return
        if bar.high == bar.high and bar.low == bar.low:  # range known (not NaN)
            if bar.high == bar.low:
                return
            close_position = (bar.close - bar.low) / (bar.high - bar.low)
            if min_close_position is not None and close_position < min_close_position:
                return

        move = (bar.close - prev_close) / prev_close * 100
        if self.max_move is None or move >= self.max_move:
            # Equal moves take the later date, as Pine does.
            self.max_move, self.max_date = move, bar.date
        if 5 <= move < 10:
            self.c5, self.d5 = self.c5 + 1, bar.date
        elif 10 <= move < 19:
            self.c10, self.d10 = self.c10 + 1, bar.date
        elif move >= 19:
            self.c19, self.d19 = self.c19 + 1, bar.date

    def result(self) -> BurstScoreResult:
        power = round(self.c5 / 5 + self.c10 / 2 + self.c19 / 0.5)
        return BurstScoreResult(
            power_score=power,
            count_5pct=self.c5,
            count_10pct=self.c10,
            count_19pct=self.c19,
            max_move_pct=self.max_move,
            max_move_date=self.max_date,
            last_date_5pct=self.d5,
            last_date_10pct=self.d10,
            last_date_19pct=self.d19,
            classification=classify_burst_score(power),
        )


def extend_burst_score(
    result: BurstScoreResult,
    prev_close: float,
    bar: DailyBar,
    min_close_position: float | None = None,
) -> BurstScoreResult:
    """`result` with one more day counted - O(1)."""
    tally = _Tally(result)
    tally.add(prev_close, bar, min_close_position)
    return tally.result()


def compute_burst_score(
    bars: Sequence[DailyBar],
    lookback_days: int | None = None,
    min_close_position: float | None = None,
    since: dt.date | None = None,
) -> BurstScoreResult:
    """
    since: count only bars dated on or after this day (Pine's calendar
    "Lookback Period" - see lookback_cutoff). The bar just before it still
    supplies the first counted day's previous close, as `close[1]` does in
    Pine.

    lookback_days: alternatively, trim to the last N bars before scoring.

    min_close_position: optional filter (0-1) requiring the day's close
    to sit at least this far up its high/low range before the move counts
    - Pine's "closing within % of highs" toggle, off by default there too.
    """
    if lookback_days is not None:
        bars = bars[-lookback_days:]
    first_counted = since.isoformat() if since is not None else ""

    tally = _Tally(EMPTY_BURST)
    for prev_bar, bar in pairwise(bars):
        if bar.date >= first_counted:
            tally.add(prev_bar.close, bar, min_close_position)
    return tally.result()


def classify_burst_score(power_score: int) -> str:
    """Pine's dot: green >= 15, orange >= 10, red below."""
    if power_score >= STRONG_POWER:
        return "strong"
    if power_score >= MODERATE_POWER:
        return "moderate"
    return "weak"
