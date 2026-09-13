"""
Builds the "typical cumulative volume by minute-of-session" curve that
makes intraday RVOL meaningful.

The naive alternative - today's partial volume over an average FULL day -
reads low all morning by construction, which is an artifact, not a signal.
Comparing 10:30 against a typical 10:30 removes that.

Verified on 2026-09-11: the feed's `vtt` matched the summed 1-minute
candle volume to within 0.013-0.038%, and no candle was stamped before
09:15. So candle-derived baselines are directly comparable to `vtt` with
no pre-open auction adjustment.
"""

from __future__ import annotations

import datetime as dt
from array import array
from collections.abc import Callable
from itertools import accumulate

from swingdash.domain.bars import MinuteBar
from swingdash.domain.calendar import IST, Session
from swingdash.domain.rvol.types import Baseline

DEFAULT_BASELINE_DAYS = 20  # also the legacy Pine script's vol_len

SessionLookup = Callable[[dt.date], Session | None]


def baseline_from_bars(
    bars: list[MinuteBar],
    session: Session,
    session_for: SessionLookup,
    days: int = DEFAULT_BASELINE_DAYS,
) -> Baseline | None:
    """
    `session` sets the curve length; `session_for` gives each historical
    day its own bounds, so a special session doesn't shift that day's curve.
    Returns None if no day in `bars` had any volume.
    """
    n_minutes = session.minutes

    by_date: dict[dt.date, list[MinuteBar]] = {}
    for bar in bars:
        moment = dt.datetime.fromisoformat(bar.ts).astimezone(IST)
        by_date.setdefault(moment.date(), []).append(bar)

    # Every trading day counts, including unusually heavy ones - a 3x
    # volume day is part of the stock's real distribution, and excluding
    # such days biased the average ~12% low (which inflated every RVOL by
    # the same amount). A genuine split shows up as a price discontinuity,
    # not merely as high volume, so it is not filtered here.
    curves: list[list[float]] = []
    for date in sorted(by_date)[-days:]:
        curve = _cumulative_curve(by_date[date], session_for(date), n_minutes)
        if curve is not None:
            curves.append(curve)

    if not curves:
        return None

    averaged = array("d", (sum(day[m] for day in curves) / len(curves) for m in range(n_minutes)))
    return Baseline(
        curve=averaged,
        avg_full_day_volume=averaged[-1],
        days_used=len(curves),
    )


def _cumulative_curve(
    day_bars: list[MinuteBar], day_session: Session | None, n_minutes: int
) -> list[float] | None:
    """
    Per-minute volume placed into a zero-filled array, then prefix-summed.
    Gaps need no special handling: a minute with no candle means no trades,
    and a zero there makes the cumulative total correctly carry forward.
    """
    if day_session is None:
        return None

    per_minute = [0.0] * n_minutes
    for bar in day_bars:
        index = day_session.minute_of(dt.datetime.fromisoformat(bar.ts))
        if index is not None and index < n_minutes:
            per_minute[index] += bar.volume

    curve = list(accumulate(per_minute))
    if curve[-1] <= 0:
        return None  # no volume all day - a halt or a stale listing
    return curve
