"""
RVOL (Relative Volume) - port of RVol.pine.

Today's volume as a percentage of the average volume over the `length`
days immediately before today (today itself is excluded from the
average) - a straight port of the Pine script's
`today_vol / sma(yday_vol, vol_len) * 100`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.engines.types import DailyBar

DEFAULT_LENGTH = 20

# The Pine script's own threshold (<=20% red, else green) was too low to
# be meaningful - almost every day would show green. These are a more
# conventional scanner convention: 200%+ is a clear volume spike worth a
# look, 120-200% is above-average. Below that isn't "weak"/bearish - low
# volume just isn't remarkable, so it's neutral rather than red (red is
# reserved for genuinely bearish signals elsewhere in the dashboard).
STRONG_THRESHOLD_PCT = 200
MODERATE_THRESHOLD_PCT = 120


@dataclass(frozen=True)
class RvolResult:
    rvol_pct: float | None
    today_volume: float
    avg_volume: float | None
    classification: str  # "strong" | "moderate" | "neutral"


def classify_rvol(rvol_pct: float | None) -> str:
    if rvol_pct is None:
        return "neutral"
    if rvol_pct >= STRONG_THRESHOLD_PCT:
        return "strong"
    if rvol_pct >= MODERATE_THRESHOLD_PCT:
        return "moderate"
    return "neutral"


def compute_rvol(bars: Sequence[DailyBar], length: int = DEFAULT_LENGTH) -> RvolResult:
    """
    bars: daily bars ordered oldest -> newest; the last bar is "today".
    Needs at least `length + 1` bars (today + `length` prior days).
    """
    if len(bars) < length + 1:
        raise ValueError(f"need at least {length + 1} bars, got {len(bars)}")

    today_volume = bars[-1].volume
    prior_window = [b.volume for b in bars[-(length + 1):-1]]
    avg_volume = sum(prior_window) / length

    if not avg_volume:
        return RvolResult(rvol_pct=None, today_volume=today_volume, avg_volume=avg_volume, classification="neutral")

    rvol_pct = round((today_volume / avg_volume) * 100)
    return RvolResult(
        rvol_pct=rvol_pct,
        today_volume=today_volume,
        avg_volume=avg_volume,
        classification=classify_rvol(rvol_pct),
    )


def compute_rvol_series(bars: Sequence[DailyBar], length: int = DEFAULT_LENGTH) -> list[float | None]:
    """
    RVOL for every bar with `length` days behind it (None before that).
    Mirrors Pine's per-bar `bar_volpct` plot - useful for charting RVOL
    over time or a future backtest, not just today's dashboard value.
    """
    volumes = [b.volume for b in bars]
    series: list[float | None] = []
    for i in range(len(volumes)):
        if i < length:
            series.append(None)
            continue
        avg = sum(volumes[i - length:i]) / length
        series.append(round((volumes[i] / avg) * 100) if avg else None)
    return series
