"""
The two RVOL numbers, as pure functions.

    rvol_intraday = vtt / baseline.curve[minute]
    rvol_day      = vtt / baseline.avg_full_day_volume

Both are ratios (1.0 = perfectly normal), not the percentages the old REST
version returned - a trader reads "3.2x" faster than "320%".

Note `curve[-1] == avg_full_day_volume` by construction, so at the closing
minute the two converge. That also means rvol_intraday IS the projected
close RVOL: projecting today's pace to the full day gives
`vtt * curve[-1]/curve[m]`, and dividing that by avg_full_day_volume
cancels back to `vtt / curve[m]`.
"""
from __future__ import annotations

from app.live.types import Baseline

# Ratio thresholds. Low volume isn't bearish, just unremarkable, so
# nothing below normal gets a negative colour - red is reserved for
# genuinely bearish signals elsewhere.
STRONG_RATIO = 2.0
MODERATE_RATIO = 1.2


def rvol_intraday(vtt: int | None, baseline: Baseline | None, minute: int | None) -> float | None:
    """Time-of-day normalised RVOL - the live signal."""
    if vtt is None or baseline is None or minute is None:
        return None
    if not 0 <= minute < len(baseline.curve):
        return None
    expected = baseline.curve[minute]
    if expected <= 0:
        return None  # illiquid name with no typical volume this early
    return vtt / expected


def rvol_day(vtt: int | None, baseline: Baseline | None) -> float | None:
    """Raw day-so-far RVOL - matches the legacy Pine script, converges at close."""
    if vtt is None or baseline is None or baseline.avg_full_day_volume <= 0:
        return None
    return vtt / baseline.avg_full_day_volume


def classify(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= STRONG_RATIO:
        return "strong"
    if ratio >= MODERATE_RATIO:
        return "moderate"
    return "neutral"
