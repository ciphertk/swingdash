"""
Replay a past session minute by minute to prove the RVOL maths.

Works with the market closed - correctness shouldn't need a live session.
Three checks:
  1. curve[-1] equals the average full-day volume over the same days (the
     identity the projected-close property depends on).
  2. At the final minute, intraday RVOL equals day RVOL.
  3. Day RVOL agrees with the legacy daily engine (the original Pine port).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from itertools import accumulate

from swingdash.domain.bars import MinuteBar
from swingdash.domain.calendar import IST
from swingdash.domain.metrics import rvol_daily
from swingdash.domain.rvol import calc
from swingdash.domain.rvol.curve import baseline_from_bars
from swingdash.services.instruments import InstrumentService
from swingdash.services.ports import HistorySource, MarketCalendar

TOLERANCE_PCT = 2.0
_CHECKPOINTS = (("09:45", 30), ("10:30", 75), ("12:00", 165), ("14:00", 285))


@dataclass(frozen=True)
class Checkpoint:
    label: str
    ratio: float | None
    pct_of_day: float


@dataclass(frozen=True)
class ReplayResult:
    symbol: str
    error: str | None = None
    session_date: dt.date | None = None
    days_used: int = 0
    curve_last: float = 0.0
    avg_daily_volume: float = 0.0
    identity_diff_pct: float = 0.0
    session_volume: float = 0.0
    final_intraday: float | None = None
    final_day: float | None = None
    legacy_ratio: float | None = None
    legacy_diff_pct: float | None = None
    checkpoints: tuple[Checkpoint, ...] = ()

    @property
    def converged(self) -> bool:
        return (
            self.final_intraday is not None
            and self.final_day is not None
            and abs(self.final_intraday - self.final_day) < 1e-9
        )

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.identity_diff_pct < TOLERANCE_PCT
            and self.converged
            and (self.legacy_diff_pct is None or self.legacy_diff_pct < TOLERANCE_PCT)
        )


def replay_symbol(
    symbol: str,
    *,
    instruments: InstrumentService,
    history: HistorySource,
    calendar: MarketCalendar,
) -> ReplayResult:
    key = instruments.find_instrument_key(symbol)
    if not key:
        return ReplayResult(symbol, error="not in instrument list")

    window_end = calendar.today() - dt.timedelta(days=1)
    window_start = window_end - dt.timedelta(days=history.max_minute_span_days - 1)
    by_date = _group_by_date(history.minute_candles(key, window_start, window_end))
    if len(by_date) < 3:
        return ReplayResult(symbol, error="not enough history to replay")

    dates = sorted(by_date)
    target_date, baseline_dates = dates[-1], dates[:-1]
    session = calendar.get_session(target_date)
    if session is None:
        return ReplayResult(symbol, error=f"{target_date} was not a trading day")

    prior_bars = [bar for date in baseline_dates for bar in by_date[date]]
    baseline = baseline_from_bars(prior_bars, session, calendar.get_session)
    if baseline is None:
        return ReplayResult(symbol, error="could not build baseline")

    # 1. identity
    day_totals = [sum(b.volume for b in by_date[d]) for d in baseline_dates[-baseline.days_used :]]
    avg_daily = sum(day_totals) / len(day_totals)
    identity_diff = abs(baseline.avg_full_day_volume - avg_daily) / avg_daily * 100

    # replay the target session as cumulative volume
    per_minute = [0.0] * session.minutes
    for bar in by_date[target_date]:
        index = session.minute_of(dt.datetime.fromisoformat(bar.ts))
        if index is not None:
            per_minute[index] += bar.volume
    cumulative = list(accumulate(per_minute))
    last = session.minutes - 1

    # 3. legacy daily engine
    daily_bars = history.daily_candles(key, window_start, target_date)
    legacy = rvol_daily.compute_rvol(daily_bars, length=baseline.days_used)
    legacy_ratio = legacy.rvol_pct / 100 if legacy.rvol_pct else None
    final_day = calc.rvol_day(int(cumulative[last]), baseline)
    legacy_diff = (
        abs(final_day - legacy_ratio) / legacy_ratio * 100
        if legacy_ratio and final_day is not None
        else None
    )

    checkpoints = tuple(
        Checkpoint(
            label,
            calc.rvol_intraday(int(cumulative[minute]), baseline, minute),
            cumulative[minute] / cumulative[last] * 100,
        )
        for label, minute in (*_CHECKPOINTS, ("close", last))
    )

    return ReplayResult(
        symbol=symbol,
        session_date=target_date,
        days_used=baseline.days_used,
        curve_last=baseline.avg_full_day_volume,
        avg_daily_volume=avg_daily,
        identity_diff_pct=identity_diff,
        session_volume=cumulative[last],
        final_intraday=calc.rvol_intraday(int(cumulative[last]), baseline, last),
        final_day=final_day,
        legacy_ratio=legacy_ratio,
        legacy_diff_pct=legacy_diff,
        checkpoints=checkpoints,
    )


def _group_by_date(bars: list[MinuteBar]) -> dict[dt.date, list[MinuteBar]]:
    grouped: dict[dt.date, list[MinuteBar]] = defaultdict(list)
    for bar in bars:
        grouped[dt.datetime.fromisoformat(bar.ts).astimezone(IST).date()].append(bar)
    return grouped
