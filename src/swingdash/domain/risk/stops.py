"""Where the stop goes: a price, a % below entry, an ATR multiple, or a recent low."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from itertools import pairwise

from swingdash.domain.bars import DailyBar


class RiskInputError(ValueError):
    """The numbers can't be sized (a message fit to show the user)."""


class StopMethod(StrEnum):
    PRICE = "price"  # value: the stop price
    PERCENT = "percent"  # value: % below entry
    ATR = "atr"  # value: multiple of ATR below entry
    RECENT_LOW = "low"  # value: sessions to take the lowest low of


def atr(bars: Sequence[DailyBar], period: int = 14) -> float | None:
    """
    Wilder's average true range over completed daily bars (as TradingView's
    `ta.atr`): the first value is the mean of `period` true ranges, then
    smoothed with weight 1/period. None until there are period + 1 bars.
    """
    if period < 1 or len(bars) < period + 1:
        return None
    ranges = [
        max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        for prev, bar in pairwise(bars)
    ]
    value = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        value = (value * (period - 1) + true_range) / period
    return value


def recent_low(bars: Sequence[DailyBar], sessions: int) -> float | None:
    """The lowest low of the last `sessions` completed bars."""
    if sessions < 1 or not bars:
        return None
    return min(bar.low for bar in bars[-sessions:])


def resolve_stop(
    method: StopMethod,
    entry: float,
    value: float,
    bars: Sequence[DailyBar] = (),
    *,
    atr_period: int = 14,
) -> float:
    if entry <= 0:
        raise RiskInputError("Entry must be above zero.")
    match method:
        case StopMethod.PRICE:
            stop = value
        case StopMethod.PERCENT:
            if not 0 < value < 100:
                raise RiskInputError("Stop % must be between 0 and 100.")
            stop = entry * (1 - value / 100)
        case StopMethod.ATR:
            if value <= 0:
                raise RiskInputError("ATR multiple must be above zero.")
            average = atr(bars, atr_period)
            if average is None:
                raise RiskInputError(f"Needs {atr_period + 1} days of history for ATR.")
            stop = entry - value * average
        case StopMethod.RECENT_LOW:
            low = recent_low(bars, int(value))
            if low is None:
                raise RiskInputError("Needs daily history for the recent low.")
            stop = low
    stop = round(stop, 2)
    if stop <= 0:
        raise RiskInputError("The stop works out at or below zero.")
    if stop >= entry:
        raise RiskInputError("The stop must be below entry (long trades only).")
    return stop
