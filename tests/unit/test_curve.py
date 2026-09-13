import datetime as dt
from itertools import pairwise

import pytest

from swingdash.domain.calendar import regular_session
from swingdash.domain.rvol.curve import baseline_from_bars
from tests.fakes.sources import minute_bars_for_day

MON, TUE, WED, THU = (dt.date(2026, 9, d) for d in (7, 8, 9, 10))
TARGET = regular_session(dt.date(2026, 9, 11))


def _lookup(date: dt.date):
    return regular_session(date)


def test_curve_end_equals_average_full_day_volume():
    bars = minute_bars_for_day(MON, {0: 100, 200: 300}) + minute_bars_for_day(
        TUE, {5: 200, 374: 400}
    )
    baseline = baseline_from_bars(bars, TARGET, _lookup)
    assert baseline is not None
    assert baseline.avg_full_day_volume == pytest.approx((400 + 600) / 2)
    assert baseline.curve[-1] == baseline.avg_full_day_volume
    assert len(baseline.curve) == 375
    assert baseline.days_used == 2


def test_curve_is_cumulative_and_carries_forward_through_gaps():
    baseline = baseline_from_bars(minute_bars_for_day(MON, {0: 10, 100: 5}), TARGET, _lookup)
    assert baseline is not None
    curve = list(baseline.curve)
    assert curve[0] == 10
    assert curve[99] == 10  # no trades between minute 0 and 100
    assert curve[100] == 15
    assert all(a <= b for a, b in pairwise(curve))


def test_heavy_days_are_not_excluded():
    """Dropping outlier-volume days once biased the baseline ~12% low."""
    normal = [minute_bars_for_day(d, {0: 1_000}) for d in (MON, TUE, WED)]
    heavy = minute_bars_for_day(THU, {0: 10_000})
    baseline = baseline_from_bars([b for day in normal for b in day] + heavy, TARGET, _lookup)
    assert baseline is not None
    assert baseline.days_used == 4
    assert baseline.avg_full_day_volume == pytest.approx(13_000 / 4)


def test_only_the_most_recent_days_are_used():
    bars = [b for d in (MON, TUE, WED, THU) for b in minute_bars_for_day(d, {0: d.day})]
    baseline = baseline_from_bars(bars, TARGET, _lookup, days=2)
    assert baseline is not None
    assert baseline.days_used == 2
    assert baseline.avg_full_day_volume == pytest.approx((WED.day + THU.day) / 2)


def test_days_without_volume_or_session_are_skipped():
    bars = minute_bars_for_day(MON, {0: 0}) + minute_bars_for_day(TUE, {0: 50})
    assert baseline_from_bars(bars, TARGET, _lookup).days_used == 1  # type: ignore[union-attr]
    assert baseline_from_bars(bars, TARGET, lambda _date: None) is None
