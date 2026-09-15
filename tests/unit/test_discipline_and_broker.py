"""'Not followed' checks, assumed-stop risk, and rebuilding positions from broker fills."""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from swingdash.domain.broker import (
    INTRADAY,
    MTF,
    NORMAL,
    BrokerHolding,
    BrokerTrade,
    Side,
    reconcile,
)
from swingdash.domain.risk.discipline import AssumedStop, Breach, breaches, position_risk
from swingdash.domain.risk.portfolio import Position, summarise

TODAY = dt.date(2026, 9, 15)
HISTORY_FROM = dt.date(2025, 9, 15)


def _position(
    entry: float = 500,
    stop: float | None = 450,
    qty: int = 100,
    **kwargs: object,
) -> Position:
    return Position(
        id=int(kwargs.pop("id", 1)),  # type: ignore[arg-type]
        symbol="X",
        instrument_key=None,
        quantity=qty,
        entry=entry,
        stop=stop,
        initial_stop=stop,
        opened_on=dt.date(2026, 9, 1),
        **kwargs,  # type: ignore[arg-type]
    )


# --- discipline ------------------------------------------------------------------


def test_a_followed_position_has_no_breaches():
    position = _position(planned_quantity=100, planned_stop=450)
    assert breaches(position, 520) == ()


@pytest.mark.parametrize(
    ("position", "price", "expected"),
    [
        (_position(stop=None), 520, (Breach.NO_STOP,)),
        (_position(stop=450), 440, (Breach.STOP_BREACHED,)),
        (_position(qty=150, planned_quantity=100), 520, (Breach.OVERSIZED,)),
        (_position(stop=420, planned_stop=450), 520, (Breach.STOP_WIDENED,)),
        (
            _position(stop=None, qty=150, planned_quantity=100),
            520,
            (Breach.NO_STOP, Breach.OVERSIZED),
        ),
    ],
)
def test_each_way_of_not_following_the_plan(position, price, expected):
    assert breaches(position, price) == expected


def test_trailing_the_stop_up_is_not_widening_it():
    assert breaches(_position(stop=480, planned_stop=450), 520) == ()


def test_closed_positions_have_nothing_to_follow():
    closed = _position(stop=None, closed_on=dt.date(2026, 9, 10), exit_price=520)
    assert breaches(closed, 520) == ()


def test_risk_to_a_real_stop_is_measured_from_entry():
    risk = position_risk(_position(stop=450), 520, 10, AssumedStop())
    assert (risk.stop, risk.assumed, risk.amount) == (450, False, 5_000)
    trailed = position_risk(_position(stop=510), 520, 10, AssumedStop())
    assert trailed.amount == 0


def test_no_stop_assumes_one_below_the_current_price():
    # 1.5 ATR (ATR 10) below 520 = 505 -> 15 x 100.
    risk = position_risk(_position(stop=None), 520, 10, AssumedStop(atr_multiple=1.5))
    assert (risk.stop, risk.assumed, risk.amount) == (505, True, 1_500)
    assert risk.breaches == (Breach.NO_STOP,)

    # Without ATR, the percentage: 8% below 520.
    fallback = position_risk(_position(stop=None), 520, None, AssumedStop(percent=8))
    assert fallback.stop == pytest.approx(478.4)
    assert fallback.amount == pytest.approx(4_160)


def test_a_breached_stop_is_replaced_by_an_assumed_one():
    risk = position_risk(_position(stop=450), 440, None, AssumedStop(percent=5))
    assert risk.assumed and risk.stop == pytest.approx(418)
    assert risk.amount == pytest.approx(2_200)
    assert risk.breaches == (Breach.STOP_BREACHED,)


def test_summary_counts_assumed_heat_and_breaches():
    followed = _position(id=1, stop=450)  # ₹5,000 to its stop
    unprotected = _position(id=2, stop=None, qty=10)
    closed = _position(id=3, closed_on=dt.date(2026, 9, 10), exit_price=520, charges=40)
    risks = {
        1: position_risk(followed, 520, None, AssumedStop()),
        2: position_risk(unprotected, 500, None, AssumedStop(percent=10)),
    }
    summary = summarise([followed, unprotected, closed], 100_000, 6, risks, prices={1: 520, 2: 500})
    assert summary.heat == pytest.approx(5_500)
    assert summary.assumed_heat == pytest.approx(500)
    assert summary.breaches == {Breach.NO_STOP: 1}
    assert summary.not_followed == 1
    assert summary.current_value == pytest.approx(100 * 520 + 10 * 500)
    assert summary.charges == 40
    assert closed.net_realised_pnl == pytest.approx(2_000 - 40)


# --- rebuilding positions from fills ------------------------------------------------

_ids = itertools.count(1)


def _fill(
    side: Side,
    qty: int,
    price: float,
    day: dt.date,
    symbol: str = "TCS",
    product: str = "CNC",
    charges: float = 0.0,
) -> BrokerTrade:
    return BrokerTrade(
        trade_id=f"T{next(_ids)}",
        symbol=symbol,
        isin="INE467B01029",
        side=side,
        product=product,
        quantity=qty,
        price=price,
        time=dt.datetime.combine(day, dt.time(10, 0)),
        charges=charges,
    )


def _held(symbol: str = "TCS", qty: int = 0, avg: float = 0.0, mtf: int = 0) -> BrokerHolding:
    return BrokerHolding(symbol, "INE467B01029", qty, avg, mtf)


def _reconcile(trades, holdings=()):
    return reconcile(trades, list(holdings), history_from=HISTORY_FROM, today=TODAY)


def test_buys_still_held_are_one_open_position():
    trades = [
        _fill(Side.BUY, 10, 100, dt.date(2026, 9, 1), charges=5),
        _fill(Side.BUY, 30, 120, dt.date(2026, 9, 3), charges=15),
    ]
    result = _reconcile(trades, [_held(qty=40, avg=115)])
    assert result.mismatches == ()
    (position,) = result.positions
    assert (position.quantity, position.entry, position.opened_on) == (40, 115, dt.date(2026, 9, 1))
    assert position.closed_on is None and position.charges == pytest.approx(20)
    assert position.ref == f"TCS:normal:open:{trades[0].trade_id}"


def test_partial_exit_closes_the_oldest_shares_first():
    trades = [
        _fill(Side.BUY, 10, 100, dt.date(2026, 9, 1)),
        _fill(Side.BUY, 10, 120, dt.date(2026, 9, 2)),
        _fill(Side.SELL, 15, 130, dt.date(2026, 9, 8), charges=30),
    ]
    result = _reconcile(trades, [_held(qty=5, avg=120)])
    assert result.mismatches == ()
    closed, still_open = result.positions
    # 10 @ 100 + 5 @ 120 sold @ 130.
    assert (closed.quantity, closed.exit_price, closed.closed_on) == (15, 130, dt.date(2026, 9, 8))
    assert closed.entry == pytest.approx((10 * 100 + 5 * 120) / 15)
    assert closed.opened_on == dt.date(2026, 9, 1)
    assert closed.charges == pytest.approx(30)
    assert (still_open.quantity, still_open.entry, still_open.opened_on) == (
        5,
        120,
        dt.date(2026, 9, 2),
    )
    # The open row keeps the holding's identity through the partial exit.
    assert still_open.ref == f"TCS:normal:open:{trades[0].trade_id}"


def test_selling_out_and_buying_again_is_a_new_position():
    trades = [
        _fill(Side.BUY, 10, 100, dt.date(2026, 8, 1)),
        _fill(Side.SELL, 10, 110, dt.date(2026, 8, 5)),
        _fill(Side.BUY, 20, 105, dt.date(2026, 9, 1)),
    ]
    result = _reconcile(trades, [_held(qty=20, avg=105)])
    closed, reopened = result.positions
    assert closed.closed_on == dt.date(2026, 8, 5)
    assert reopened.ref == f"TCS:normal:open:{trades[2].trade_id}"
    assert reopened.opened_on == dt.date(2026, 9, 1)


def test_shares_older_than_the_history_come_from_holdings():
    trades = [_fill(Side.BUY, 5, 200, dt.date(2026, 9, 1))]
    result = _reconcile(trades, [_held(qty=15, avg=150)])
    assert result.mismatches == ()
    (position,) = result.positions
    assert position.quantity == 15
    assert position.entry == pytest.approx((10 * 150 + 5 * 200) / 15)
    assert position.opened_on == HISTORY_FROM
    assert "held before" in position.note


def test_todays_fills_are_not_in_holdings_yet():
    trades = [_fill(Side.BUY, 10, 100, TODAY)]
    result = _reconcile(trades, [])  # holdings don't show today's buy
    assert result.mismatches == ()
    assert result.positions[0].quantity == 10


def test_unexplained_quantities_are_reported():
    # Holdings show nothing, but the trades say 10 are still held.
    trades = [_fill(Side.BUY, 10, 100, dt.date(2026, 9, 1))]
    result = _reconcile(trades, [])
    assert any("broker shows 0" in m for m in result.mismatches)

    oversold = _reconcile([_fill(Side.SELL, 5, 100, dt.date(2026, 9, 1))], [])
    assert any("sold 5 more" in m for m in oversold.mismatches)


def test_same_day_exits_are_closed_trades_kept_apart_from_holdings():
    # A swing buy whose stop hit the same day shows up at the broker as intraday.
    trades = [
        _fill(Side.BUY, 100, 200, dt.date(2026, 9, 1)),  # delivery, still held
        _fill(Side.BUY, 10, 100, dt.date(2026, 9, 3), product="INTRADAY", charges=4),
        _fill(Side.SELL, 10, 95, dt.date(2026, 9, 3), product="INTRADAY", charges=4),
        _fill(Side.BUY, 2, 50, dt.date(2026, 9, 4), product=""),  # blank product: same
        _fill(Side.SELL, 2, 52, dt.date(2026, 9, 4), product=""),
        _fill(Side.BUY, 50, 100, dt.date(2026, 9, 2), product="MTF"),
    ]
    result = _reconcile(trades, [_held(qty=100, avg=200, mtf=50)])
    assert result.mismatches == ()
    by_funding = {}
    for position in result.positions:
        by_funding.setdefault(position.funding, []).append(position)

    first, second = sorted(by_funding[INTRADAY], key=lambda p: p.opened_on)
    assert (first.quantity, first.entry, first.exit_price, first.charges) == (10, 100, 95, 8)
    assert first.opened_on == first.closed_on == dt.date(2026, 9, 3)
    assert first.note == "intraday - exited the same day"
    assert second.exit_price == 52
    # The delivery shares weren't touched by the same-day exit.
    (held,) = by_funding[NORMAL]
    assert (held.quantity, held.entry, held.closed_on) == (100, 200, None)
    (mtf,) = by_funding[MTF]
    assert mtf.quantity == 50


def test_an_intraday_buy_left_open_is_reported():
    result = _reconcile([_fill(Side.BUY, 10, 100, dt.date(2026, 9, 3), product="INTRADAY")])
    assert result.positions == ()
    assert any("wasn't sold the same day" in m for m in result.mismatches)


def test_dp_is_estimated_for_broker_delivery_exits_only():
    closed = {"closed_on": dt.date(2026, 9, 10), "exit_price": 520}
    dhan = _position(source="dhan", **closed)
    assert dhan.dp_estimate == pytest.approx(12.5 * 1.18)
    assert _position(source="zerodha", **closed).dp_estimate == pytest.approx(15.34)
    assert _position(source="dhan", funding="intraday", **closed).dp_estimate is None
    assert _position(**closed).dp_estimate is None  # manual: no broker to go by
    assert _position(source="dhan").dp_estimate is None  # still open
