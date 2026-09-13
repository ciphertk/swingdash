"""
EMA (Exponential Moving Average) - generic smoothing shared by any engine
that needs it. Today that's Mswing's 9-period smoothing line; later it's
also the standalone 20/50-day price EMA metric from CLAUDE.md's glossary.
Keeping the math in one place means both get it for free and can't drift.
"""

from __future__ import annotations

from collections.abc import Sequence


def compute_ema_series(values: Sequence[float], length: int) -> list[float | None]:
    """
    Seeds with the SMA of the first `length` values, then applies the
    standard EMA recurrence forward. Returns None for indices before the
    seed point (mirrors Pine's `na` for insufficient history).
    """
    if length <= 0:
        raise ValueError("length must be positive")
    if len(values) < length:
        return [None] * len(values)

    alpha = 2 / (length + 1)
    series: list[float | None] = [None] * (length - 1)

    prev = sum(values[:length]) / length
    series.append(prev)

    for value in values[length:]:
        prev = (value - prev) * alpha + prev
        series.append(prev)

    return series


def compute_ema(values: Sequence[float], length: int) -> float | None:
    """Latest EMA value only."""
    series = compute_ema_series(values, length)
    return series[-1] if series else None
