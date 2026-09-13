import random

import pytest

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.ema import compute_ema_series
from swingdash.domain.metrics.mswing import (
    classify_mswing,
    compute_mswing_series,
    live_mswing,
    prepare_mswing,
)


def _bars(closes: list[float]) -> list[DailyBar]:
    return [DailyBar(f"d{i:04d}", c, c, c, c, 0) for i, c in enumerate(closes)]


def _random_closes(n: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    price, closes = 100.0, []
    for _ in range(n):
        price *= 1 + rng.uniform(-0.06, 0.07)
        closes.append(round(price, 2))
    return closes


def _reference_series(closes: list[float], l1: int, l2: int, ipo: bool) -> list[float | None]:
    """The original O(n^2) port: momentum recomputed on a sliced window per bar."""

    def momentum(window: list[float], length: int) -> float:
        if len(window) < 2:
            raise ValueError
        actual = (min(length, len(window) - 1) or length) if ipo else length
        if len(window) <= actual:
            raise ValueError
        past = window[-1 - actual]
        return 0.0 if past == 0 else (window[-1] - past) * 100 / past / actual

    out: list[float | None] = []
    for i in range(len(closes)):
        try:
            out.append(momentum(closes[: i + 1], l1) + momentum(closes[: i + 1], l2))
        except ValueError:
            out.append(None)
    return out


@pytest.mark.parametrize("ipo", [True, False])
@pytest.mark.parametrize("n", [0, 1, 2, 19, 21, 50, 51, 130])
def test_linear_series_matches_the_original_quadratic_port(n, ipo):
    closes = _random_closes(n, seed=n)
    assert compute_mswing_series(_bars(closes), ipo_adjusted=ipo) == _reference_series(
        closes, 20, 50, ipo
    )


@pytest.mark.parametrize("ema_length", [1, 9])
@pytest.mark.parametrize("n", [1, 2, 9, 10, 11, 49, 50, 51, 60, 300])
def test_live_update_equals_recomputing_with_the_new_close(n, ema_length):
    """One O(1) tick must give exactly what a full recompute would."""
    closes = _random_closes(n + 1, seed=1000 + n)
    history, new_close = _bars(closes[:-1]), closes[-1]

    live = live_mswing(prepare_mswing(history, ema_length=ema_length), new_close)
    full = prepare_mswing(_bars(closes), ema_length=ema_length).last

    for field in ("score", "momentum_short", "momentum_long", "ema"):
        got, expected = getattr(live, field), getattr(full, field)
        assert (got is None) == (expected is None), field
        if expected is not None:
            assert got == pytest.approx(expected), field


def test_without_a_new_close_live_returns_the_last_completed_bar():
    bars = _bars(_random_closes(80, seed=7))
    ctx = prepare_mswing(bars)
    assert live_mswing(ctx, None) == ctx.last
    scores = [s for s in compute_mswing_series(bars) if s is not None]
    assert ctx.last.score == scores[-1]
    assert ctx.last.ema == compute_ema_series(scores, 9)[-1]


def test_empty_history_has_no_value():
    ctx = prepare_mswing([])
    assert live_mswing(ctx, 101.0).score is None


@pytest.mark.parametrize(
    ("stock", "index", "expected"),
    [
        (1.5, 1.0, "strong"),
        (0.5, 1.0, "neutral"),
        (-0.5, -1.0, "neutral"),
        (-1.5, -1.0, "weak"),
        (0.0, -1.0, "weak"),
    ],
)
def test_classification_matches_the_pine_table(stock, index, expected):
    assert classify_mswing(stock, index) == expected
