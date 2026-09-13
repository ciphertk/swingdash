"""When today's price joins the Scanner's numbers - weekends, intraday, after the close."""

import datetime as dt

from swingdash.domain.bars import DailyBar
from swingdash.domain.metrics.mswing import live_mswing
from swingdash.domain.scanner import SymbolContext, TodayBar, live_symbol, prepare_symbol

THU, FRI, MON = dt.date(2026, 9, 10), dt.date(2026, 9, 11), dt.date(2026, 9, 15)


def _jump(ctx: SymbolContext, factor: float = 1.12) -> float:
    """A price `factor` times the last close - 1.12 is a 12% day."""
    assert ctx.last_close is not None
    return ctx.last_close * factor


def _history(through: dt.date, days: int = 80) -> list[DailyBar]:
    bars, price = [], 100.0
    day = through - dt.timedelta(days=days - 1)
    while day <= through:
        price *= 1.004
        bars.append(DailyBar(day.isoformat(), price, price * 1.01, price * 0.99, price, 1000))
        day += dt.timedelta(days=1)
    return bars


def test_weekend_shows_the_last_session_without_counting_it_twice():
    ctx = prepare_symbol(_history(FRI), FRI)
    metrics = live_symbol(ctx, TodayBar(FRI, THU, ltp=ctx.last_close, closed=True))
    assert not metrics.includes_today
    assert metrics.burst == ctx.burst
    assert metrics.mswing == ctx.mswing.last
    assert metrics.change_pct is not None  # Friday's own change


def test_intraday_moves_mswing_but_not_burst_power():
    ctx = prepare_symbol(_history(FRI), MON)
    ltp = _jump(ctx)  # a 12% day - but not a completed one yet

    metrics = live_symbol(ctx, TodayBar(MON, FRI, ltp=ltp, closed=False))

    assert metrics.includes_today
    assert metrics.burst == ctx.burst
    assert metrics.mswing == live_mswing(ctx.mswing, ltp)
    assert metrics.change_pct is not None and round(metrics.change_pct) == 12


def test_after_the_close_todays_move_counts_toward_burst_power():
    ctx = prepare_symbol(_history(FRI), MON)
    metrics = live_symbol(ctx, TodayBar(MON, FRI, ltp=_jump(ctx), closed=True))
    assert metrics.burst.count_10pct == ctx.burst.count_10pct + 1
    assert metrics.burst.last_date_10pct == MON.isoformat()


def test_stale_history_is_shown_as_is_rather_than_skipping_a_day():
    ctx = prepare_symbol(_history(THU), MON)  # Friday's candle not fetched yet
    metrics = live_symbol(ctx, TodayBar(MON, FRI, ltp=_jump(ctx), closed=False))
    assert not metrics.includes_today


def test_no_price_yet_falls_back_to_history():
    ctx = prepare_symbol(_history(FRI), MON)
    assert not live_symbol(ctx, TodayBar(MON, FRI, ltp=None, closed=False)).includes_today
