"""
Mswing - port of Mswing Homma.pine.

Sums 20-day and 50-day momentum (% change over the period, divided by the
period - a per-bar rate) into one score, then compares it against the
same score computed for a benchmark index to classify the stock as
outperforming, lagging, or roughly in line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.ema import compute_ema

LENGTH_SHORT = 20
LENGTH_LONG = 50
DEFAULT_EMA_LENGTH = 9


def _closes(bars: Sequence[DailyBar]) -> list[float]:
    return [b.close for b in bars]


def compute_momentum(closes: Sequence[float], length: int, ipo_adjusted: bool = True) -> float:
    """
    Port of Pine's f_momentum: % change over `length` bars, divided by
    `length`. `ipo_adjusted` mirrors the original's IPO-aware lookback -
    if there isn't `length` bars of history yet (a recent listing), it
    falls back to however many bars actually exist instead of erroring,
    same as the Pine script's `enable_ipo_mswing` toggle.
    """
    if len(closes) < 2:
        raise ValueError("need at least 2 closes")

    if ipo_adjusted:
        actual_len = min(length, len(closes) - 1) or length
    else:
        actual_len = length

    if len(closes) <= actual_len:
        raise ValueError(f"need at least {actual_len + 1} closes for length {length}")

    past = closes[-1 - actual_len]
    if past == 0:
        return 0.0
    return (closes[-1] - past) * 100 / past / actual_len


def compute_mswing_series(
    bars: Sequence[DailyBar],
    length1: int = LENGTH_SHORT,
    length2: int = LENGTH_LONG,
    ipo_adjusted: bool = True,
) -> list[float | None]:
    """
    Stock-only Mswing score at every bar (no index comparison) - feed into
    compute_ema for the smoothed line the Pine plot draws (mswing_ma).
    """
    closes = _closes(bars)
    series: list[float | None] = []
    for i in range(len(closes)):
        window = closes[: i + 1]
        try:
            series.append(
                compute_momentum(window, length1, ipo_adjusted)
                + compute_momentum(window, length2, ipo_adjusted)
            )
        except ValueError:
            series.append(None)
    return series


def classify_mswing(mswing: float, index_mswing: float) -> str:
    """
    Mirrors the Pine table's color logic exactly, branch for branch:
      - strong:  positive AND beating the index
      - neutral: positive-but-lagging, or negative-but-beating
      - weak:    everything else (negative-and-lagging, or exactly zero)
    """
    if mswing > 0 and mswing >= index_mswing:
        return "strong"
    if mswing > 0 and mswing < index_mswing:
        return "neutral"
    if mswing < 0 and mswing >= index_mswing:
        return "neutral"
    return "weak"


@dataclass(frozen=True)
class MswingResult:
    mswing: float
    mswing_ma: float | None
    index_mswing: float
    classification: str


def compute_mswing(
    bars: Sequence[DailyBar],
    index_bars: Sequence[DailyBar],
    length1: int = LENGTH_SHORT,
    length2: int = LENGTH_LONG,
    ema_length: int = DEFAULT_EMA_LENGTH,
    ipo_adjusted: bool = True,
) -> MswingResult:
    """
    bars: the stock's daily candles, oldest -> newest.
    index_bars: a benchmark index's daily candles over the same dates -
    the original Pine script compared against NIFTYMIDSML400. Which
    index/instrument_key to use is an ingestion-layer decision, not this
    engine's - it just takes whatever closes it's handed.
    """
    closes = _closes(bars)
    index_closes = _closes(index_bars)

    mswing = compute_momentum(closes, length1, ipo_adjusted) + compute_momentum(
        closes, length2, ipo_adjusted
    )
    index_mswing = compute_momentum(index_closes, length1, ipo_adjusted) + compute_momentum(
        index_closes, length2, ipo_adjusted
    )

    mswing_series = [
        v for v in compute_mswing_series(bars, length1, length2, ipo_adjusted) if v is not None
    ]
    mswing_ma = compute_ema(mswing_series, ema_length) if len(mswing_series) >= ema_length else None

    return MswingResult(
        mswing=mswing,
        mswing_ma=mswing_ma,
        index_mswing=index_mswing,
        classification=classify_mswing(mswing, index_mswing),
    )
