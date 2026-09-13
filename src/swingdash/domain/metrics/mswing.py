"""
Mswing - port of Mswing Homma.pine.

Sums 20-day and 50-day momentum (% change over the period, divided by the
period - a per-bar rate) into one score, then compares it against the
same score computed for a benchmark index to classify the stock as
outperforming, lagging, or roughly in line.

Live use is split in two so a price tick costs a few float operations:
`prepare_mswing` walks the completed daily history once per session, and
`live_mswing` treats the current price as today's close - the value
TradingView shows on the last bar while the market is open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.ema import compute_ema, compute_ema_series

LENGTH_SHORT = 20
LENGTH_LONG = 50
DEFAULT_EMA_LENGTH = 9


def _closes(bars: Sequence[DailyBar]) -> list[float]:
    return [b.close for b in bars]


def _momentum_at(
    closes: Sequence[float], index: int, length: int, ipo_adjusted: bool
) -> float | None:
    """
    Pine's f_momentum at bar `index`: % change over `length` bars, divided
    by `length`. IPO-adjusted, a stock with less history uses however many
    bars exist (Pine's `enable_ipo_mswing`). None where Pine gives na.
    """
    actual = _span(index, length, ipo_adjusted)
    return None if actual is None else _momentum(closes[index], closes[index - actual], actual)


def _span(index: int, length: int, ipo_adjusted: bool) -> int | None:
    """How many bars back momentum looks from bar `index`, or None (Pine's na)."""
    if index < 1:
        return None
    actual = min(length, index) if ipo_adjusted else length
    return None if index < actual else actual


def _momentum(close: float, past: float, span: int) -> float:
    return 0.0 if past == 0 else (close - past) * 100 / past / span


def compute_momentum(closes: Sequence[float], length: int, ipo_adjusted: bool = True) -> float:
    """Momentum at the latest close. Raises ValueError without enough history."""
    value = _momentum_at(closes, len(closes) - 1, length, ipo_adjusted)
    if value is None:
        raise ValueError(f"not enough closes ({len(closes)}) for momentum over {length}")
    return value


def _score_at(
    closes: Sequence[float], index: int, length1: int, length2: int, ipo_adjusted: bool
) -> float | None:
    short = _momentum_at(closes, index, length1, ipo_adjusted)
    long = _momentum_at(closes, index, length2, ipo_adjusted)
    return None if short is None or long is None else short + long


def compute_mswing_series(
    bars: Sequence[DailyBar],
    length1: int = LENGTH_SHORT,
    length2: int = LENGTH_LONG,
    ipo_adjusted: bool = True,
) -> list[float | None]:
    """Stock-only Mswing score at every bar, O(n)."""
    closes = _closes(bars)
    return [_score_at(closes, i, length1, length2, ipo_adjusted) for i in range(len(closes))]


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


# --- live: prepare once, update per tick --------------------------------------


@dataclass(frozen=True)
class MswingValue:
    score: float | None
    momentum_short: float | None
    momentum_long: float | None
    ema: float | None


NO_MSWING = MswingValue(None, None, None, None)


@dataclass(frozen=True)
class MswingContext:
    """Everything a new close needs, taken from completed history."""

    bar_count: int
    # The last max(length1, length2) closes - the furthest back a new bar looks.
    tail_closes: tuple[float, ...]
    # EMA of the score up to the last completed bar, once seeded.
    ema: float | None
    score_count: int
    # Scores still needed to seed the EMA (fewer than ema_length so far).
    seed_scores: tuple[float, ...]
    last: MswingValue
    length1: int
    length2: int
    ema_length: int
    ipo_adjusted: bool


def prepare_mswing(
    bars: Sequence[DailyBar],
    length1: int = LENGTH_SHORT,
    length2: int = LENGTH_LONG,
    ema_length: int = DEFAULT_EMA_LENGTH,
    ipo_adjusted: bool = True,
) -> MswingContext:
    closes = _closes(bars)
    scores = [
        s
        for s in (_score_at(closes, i, length1, length2, ipo_adjusted) for i in range(len(closes)))
        if s is not None
    ]
    ema_series = compute_ema_series(scores, ema_length) if scores else []
    ema = ema_series[-1] if ema_series else None

    last_index = len(closes) - 1
    last = (
        MswingValue(
            score=_score_at(closes, last_index, length1, length2, ipo_adjusted),
            momentum_short=_momentum_at(closes, last_index, length1, ipo_adjusted),
            momentum_long=_momentum_at(closes, last_index, length2, ipo_adjusted),
            ema=ema,
        )
        if closes
        else NO_MSWING
    )
    return MswingContext(
        bar_count=len(closes),
        tail_closes=tuple(closes[-max(length1, length2) :]),
        ema=ema,
        score_count=len(scores),
        seed_scores=tuple(scores[-(ema_length - 1) :]) if ema is None and ema_length > 1 else (),
        last=last,
        length1=length1,
        length2=length2,
        ema_length=ema_length,
        ipo_adjusted=ipo_adjusted,
    )


def live_mswing(ctx: MswingContext, close: float | None) -> MswingValue:
    """
    Mswing with `close` as a new bar after the prepared history - O(1).
    None returns the last completed bar's value.
    """
    if close is None:
        return ctx.last

    def momentum(length: int) -> float | None:
        # The new bar sits at index bar_count; `span` bars back is always
        # inside the tail, which holds the last max(length1, length2) closes.
        span = _span(ctx.bar_count, length, ctx.ipo_adjusted)
        if span is None:
            return None
        return _momentum(close, ctx.tail_closes[len(ctx.tail_closes) - span], span)

    short = momentum(ctx.length1)
    long = momentum(ctx.length2)
    score = None if short is None or long is None else short + long

    ema = ctx.ema
    if score is not None:
        alpha = 2 / (ctx.ema_length + 1)
        if ctx.ema is not None:
            ema = (score - ctx.ema) * alpha + ctx.ema
        elif ctx.score_count + 1 >= ctx.ema_length:
            seed = (*ctx.seed_scores, score)[-ctx.ema_length :]
            ema = sum(seed) / ctx.ema_length
    return MswingValue(score=score, momentum_short=short, momentum_long=long, ema=ema)


# --- one-shot (stock vs index) -----------------------------------------------


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
    the original Pine script compared against NIFTYMIDSML400.
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
