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
from itertools import accumulate

from swingdash.db import get_connection, transaction
from swingdash.engines.types import MinuteBar
from swingdash.live.session import IST, Session, get_session
from swingdash.live.types import Baseline
from swingdash.services import ingestion_service

DEFAULT_BASELINE_DAYS = 20  # also the legacy Pine script's vol_len


def build_baseline(
    instrument_key: str,
    session: Session,
    days: int = DEFAULT_BASELINE_DAYS,
) -> Baseline | None:
    """
    One REST call per symbol (a ~28 calendar day window stays inside
    Upstox's 1-month cap for 1-minute data). Returns None if the symbol
    has no usable history at all.
    """
    to_date = session.date - dt.timedelta(days=1)  # history excludes today
    from_date = to_date - dt.timedelta(days=ingestion_service.MAX_MINUTE_HISTORY_DAYS - 1)

    bars = ingestion_service.fetch_minute_candles(instrument_key, from_date, to_date)
    if not bars:
        return None

    return baseline_from_bars(bars, session, days)


def baseline_from_bars(
    bars: list[MinuteBar],
    session: Session,
    days: int = DEFAULT_BASELINE_DAYS,
) -> Baseline | None:
    """
    Pure - separated from the fetch so the replay harness and tests can
    build baselines from canned candles without touching the network.
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
        curve = _cumulative_curve(by_date[date], date, n_minutes)
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
    day_bars: list[MinuteBar], date: dt.date, n_minutes: int
) -> list[float] | None:
    """
    Per-minute volume placed into a zero-filled array, then prefix-summed.
    Gaps need no special handling: a minute with no candle means no trades,
    and a zero there makes the cumulative total correctly carry forward.

    Minute indices come from that day's OWN session bounds (cached, so this
    costs at most one API call per date across the whole watchlist), not
    from today's - otherwise a special session would shift the curve.
    """
    day_session = get_session(date)
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


# --- persistence -----------------------------------------------------------
# Keyed by session_date so a same-day restart is a pure read and the row
# goes stale on its own tomorrow.


def load_many(instrument_keys: list[str], session_date: dt.date) -> dict[str, Baseline]:
    if not instrument_keys:
        return {}

    placeholders = ",".join("?" * len(instrument_keys))
    rows = (
        get_connection()
        .execute(
            f"""
        SELECT instrument_key, days_used, avg_full_day_volume, curve
        FROM rvol_baseline
        WHERE session_date = ? AND instrument_key IN ({placeholders})
        """,
            (session_date.isoformat(), *instrument_keys),
        )
        .fetchall()
    )

    out: dict[str, Baseline] = {}
    for row in rows:
        curve = array("d")
        curve.frombytes(row["curve"])
        out[row["instrument_key"]] = Baseline(
            curve=curve,
            avg_full_day_volume=row["avg_full_day_volume"],
            days_used=row["days_used"],
        )
    return out


def save(instrument_key: str, session_date: dt.date, baseline: Baseline) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO rvol_baseline
                (instrument_key, session_date, days_used, avg_full_day_volume, curve, built_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_key, session_date) DO UPDATE SET
                days_used = excluded.days_used,
                avg_full_day_volume = excluded.avg_full_day_volume,
                curve = excluded.curve,
                built_at = excluded.built_at
            """,
            (
                instrument_key,
                session_date.isoformat(),
                baseline.days_used,
                baseline.avg_full_day_volume,
                baseline.curve.tobytes(),
                dt.datetime.now(dt.UTC).isoformat(),
            ),
        )
