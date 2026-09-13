import datetime as dt
import math

import pytest

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.burst_score import (
    EMPTY_BURST,
    classify_burst_score,
    compute_burst_score,
    extend_burst_score,
    lookback_cutoff,
)


def _bars(*closes: float, start: dt.date = dt.date(2026, 1, 1)) -> list[DailyBar]:
    """Daily bars with a real high/low range, one calendar day apart."""
    return [
        DailyBar((start + dt.timedelta(days=i)).isoformat(), c, c * 1.01, c * 0.99, c, 1000)
        for i, c in enumerate(closes)
    ]


def test_move_buckets_and_power_formula():
    #          +5%    +9.99%        +10%     +19%     -20%
    closes = [100, 105, 105 * 1.0999, 100, 110, 100, 119, 95.2]
    result = compute_burst_score(_bars(*closes))
    assert (result.count_5pct, result.count_10pct, result.count_19pct) == (2, 1, 1)
    assert result.power_score == round(2 / 5 + 1 / 2 + 1 / 0.5)  # 3
    assert result.max_move_pct == pytest.approx(19.0)


def test_equal_max_moves_take_the_later_date():
    result = compute_burst_score(_bars(100, 110, 100, 110))
    assert result.max_move_date == "2026-01-04"


def test_since_counts_the_first_day_using_the_close_before_the_cutoff():
    """Pine's `close[1]` exists for the first in-window bar even though it's before the cutoff."""
    bars = _bars(100, 106, 100, 100)
    result = compute_burst_score(bars, since=dt.date(2026, 1, 2))
    assert result.count_5pct == 1
    assert result.last_date_5pct == "2026-01-02"
    assert compute_burst_score(bars, since=dt.date(2026, 1, 3)).count_5pct == 0


def test_a_flat_bar_is_skipped_but_an_unknown_range_counts():
    flat = DailyBar("2026-01-02", 110, 110, 110, 110, 0)
    assert extend_burst_score(EMPTY_BURST, 100, flat) == EMPTY_BURST
    unknown = DailyBar("2026-01-02", math.nan, math.nan, math.nan, 110, 0)
    assert extend_burst_score(EMPTY_BURST, 100, unknown).count_10pct == 1


def test_extending_by_one_day_equals_recomputing():
    bars = _bars(100, 104, 111, 108, 130, 129, 136)
    extended = extend_burst_score(compute_burst_score(bars[:-1]), bars[-2].close, bars[-1])
    assert extended == compute_burst_score(bars)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 9, 13), dt.date(2023, 9, 13)),
        (dt.date(2028, 2, 29), dt.date(2025, 3, 1)),  # no 29 Feb in 2025
    ],
)
def test_lookback_cutoff(today, expected):
    assert lookback_cutoff(today) == expected


@pytest.mark.parametrize(
    ("power", "expected"), [(15, "strong"), (14, "moderate"), (10, "moderate"), (9, "weak")]
)
def test_classification_matches_pines_dot(power, expected):
    assert classify_burst_score(power) == expected
