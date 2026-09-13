from array import array

import pytest

from swingdash.domain.rvol import calc
from swingdash.domain.rvol.types import Baseline


def _baseline(curve: list[float]) -> Baseline:
    return Baseline(curve=array("d", curve), avg_full_day_volume=curve[-1], days_used=20)


def test_intraday_rvol_compares_against_the_same_minute():
    baseline = _baseline([100, 200, 400])
    assert calc.rvol_intraday(300, baseline, minute=1) == pytest.approx(1.5)


def test_day_rvol_compares_against_full_day_average():
    baseline = _baseline([100, 200, 400])
    assert calc.rvol_day(300, baseline) == pytest.approx(0.75)


def test_intraday_and_day_converge_at_the_final_minute():
    baseline = _baseline([100, 250, 400])
    assert calc.rvol_intraday(520, baseline, minute=2) == calc.rvol_day(520, baseline)


@pytest.mark.parametrize(
    ("vtt", "minute"),
    [(None, 1), (300, None), (300, -1), (300, 3)],
)
def test_intraday_rvol_is_none_without_usable_inputs(vtt, minute):
    assert calc.rvol_intraday(vtt, _baseline([100, 200, 400]), minute) is None


def test_zero_expected_volume_yields_none_not_a_crash():
    assert calc.rvol_intraday(50, _baseline([0, 0, 10]), minute=0) is None
    assert calc.rvol_day(50, _baseline([0, 0, 0])) is None


@pytest.mark.parametrize(
    ("ratio", "bucket"),
    [(None, "unknown"), (0.3, "neutral"), (1.19, "neutral"), (1.2, "moderate"), (2.0, "strong")],
)
def test_classify_never_marks_low_volume_as_bearish(ratio, bucket):
    assert calc.classify(ratio) == bucket
