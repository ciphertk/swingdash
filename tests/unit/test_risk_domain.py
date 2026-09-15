"""Position sizing, stops, charges, warnings and portfolio heat."""

import datetime as dt

import pytest

from swingdash.domain.bars import DailyBar
from swingdash.domain.risk.charges import NO_CHARGES, UPSTOX_DELIVERY, round_trip
from swingdash.domain.risk.checks import Severity, TradeContext, trade_warnings
from swingdash.domain.risk.funding import Funding
from swingdash.domain.risk.portfolio import Position, summarise
from swingdash.domain.risk.sizing import (
    Limit,
    RiskMode,
    RiskSpec,
    SizingInput,
    SizingResult,
    size_position,
)
from swingdash.domain.risk.stops import (
    RiskInputError,
    StopMethod,
    atr,
    recent_low,
    resolve_stop,
)
from swingdash.domain.securities import PriceBand, Surveillance

ONE_PCT = RiskSpec(RiskMode.PERCENT, 1.0)


def _bar(high: float, low: float, close: float, day: int = 1, volume: float = 1e5) -> DailyBar:
    return DailyBar(f"2026-09-{day:02d}", close, high, low, close, volume)


# --- stops -----------------------------------------------------------------


def test_atr_is_wilders_smoothing_of_true_ranges():
    bars = [
        _bar(10, 10, 10),
        _bar(12, 9, 11),  # TR 3 (high - low)
        _bar(11.5, 10.5, 11),  # TR 1
        _bar(14, 11, 13),  # TR 3
    ]
    # First ATR(2) = mean(3, 1) = 2; then (2 * 1 + 3) / 2 = 2.5.
    assert atr(bars, 2) == pytest.approx(2.5)
    assert atr(bars[:2], 2) is None  # needs period + 1 bars


def test_true_range_counts_gaps_from_the_previous_close():
    bars = [_bar(100, 100, 100), _bar(111, 110, 110)]  # gap up: TR = 111 - 100
    assert atr(bars, 1) == pytest.approx(11)


def test_recent_low_looks_back_n_sessions():
    bars = [_bar(10, 5, 8), _bar(10, 7, 9), _bar(10, 6, 9)]
    assert recent_low(bars, 2) == 6
    assert recent_low(bars, 10) == 5


@pytest.mark.parametrize(
    ("method", "value", "stop"),
    [
        (StopMethod.PRICE, 450, 450),
        (StopMethod.PERCENT, 8, 460),
        (StopMethod.ATR, 1.5, 469.25),  # true ranges 21, 20 -> ATR(2) 20.5 -> 500 - 30.75
        (StopMethod.RECENT_LOW, 2, 480),
    ],
)
def test_resolve_stop(method, value, stop):
    # A gap down and back: true ranges include the gaps; the lowest low of the last 2 is 480.
    bars = [_bar(502, 500, 501), _bar(482, 480, 481), _bar(501, 499, 500)]
    assert resolve_stop(method, 500, value, bars, atr_period=2) == pytest.approx(stop)


@pytest.mark.parametrize(
    ("method", "value", "bars", "message"),
    [
        (StopMethod.PRICE, 510, [], "below entry"),
        (StopMethod.PERCENT, 0, [], "between 0 and 100"),
        (StopMethod.ATR, 1.5, [], "history for ATR"),
        (StopMethod.RECENT_LOW, 10, [], "history"),
        (StopMethod.RECENT_LOW, 10, [_bar(520, 505, 510)], "below entry"),
    ],
)
def test_unusable_stops_explain_themselves(method, value, bars, message):
    with pytest.raises(RiskInputError, match=message):
        resolve_stop(method, 500, value, bars, atr_period=2)


# --- charges ---------------------------------------------------------------


def test_round_trip_charges_match_a_worked_example():
    # Buy 100 @ 500 (₹50,000), sell 100 @ 450 (₹45,000), Upstox delivery rates.
    charges = round_trip(UPSTOX_DELIVERY, 100, 500, 450)
    buy = 20 + 50 + 1.535 + 0.05 + 7.5 + 0.18 * (20 + 1.535)
    sell = 20 + 45 + 1.3815 + 0.045 + 20 + 0.18 * (20 + 1.3815 + 20)
    assert charges.buy == pytest.approx(buy)
    assert charges.sell == pytest.approx(sell)
    assert charges.total == pytest.approx(176.836, abs=0.01)
    assert round_trip(UPSTOX_DELIVERY, 0, 500, 450).total == 0


# --- sizing ----------------------------------------------------------------


def test_risk_bound_quantity_includes_charges():
    result = size_position(SizingInput(capital=1_000_000, risk=ONE_PCT, entry=500, stop=450))

    def risk_at(quantity: int) -> float:
        return quantity * 50 + round_trip(UPSTOX_DELIVERY, quantity, 500, 450).total

    assert result.limited_by is Limit.RISK
    # The most shares whose loss at the stop, charges included, fits ₹10,000.
    assert risk_at(result.quantity) <= 10_000 < risk_at(result.quantity + 1)
    assert result.quantity == 194
    assert result.total_risk <= 10_000 and result.risk_pct <= 1.0
    assert result.position_value == 194 * 500
    assert (result.target(1), result.target(2), result.target(3)) == (550, 600, 650)


def test_without_charges_it_is_plain_division():
    spec = SizingInput(1_000_000, ONE_PCT, 500, 450, charges=NO_CHARGES)
    assert size_position(spec).quantity == 200


def test_fixed_amount_risk():
    spec = SizingInput(1_000_000, RiskSpec(RiskMode.AMOUNT, 5_000), 500, 450, charges=NO_CHARGES)
    assert size_position(spec).quantity == 100


def test_a_tight_stop_is_capped_by_allocation():
    spec = SizingInput(1_000_000, ONE_PCT, 500, 495, max_allocation_pct=20, charges=NO_CHARGES)
    result = size_position(spec)
    assert result.limited_by is Limit.ALLOCATION
    assert result.quantity == 400  # ₹2,00,000 / 500
    assert result.allowed[Limit.RISK] == 2_000
    assert result.allocation_pct == pytest.approx(20)


def test_free_capital_and_heat_limits():
    free = SizingInput(1_000_000, ONE_PCT, 500, 495, free_capital=60_000, charges=NO_CHARGES)
    assert size_position(free).limited_by is Limit.FREE_CAPITAL
    assert size_position(free).quantity == 120

    heat = SizingInput(1_000_000, ONE_PCT, 500, 450, heat_left=2_500, charges=NO_CHARGES)
    result = size_position(heat)
    assert (result.limited_by, result.quantity, result.risk_budget) == (Limit.HEAT, 50, 2_500)


def test_sme_quantities_are_whole_lots():
    spec = SizingInput(1_000_000, ONE_PCT, 500, 450, lot_size=60, charges=NO_CHARGES)
    assert size_position(spec).quantity == 180  # 200 shares -> 3 lots


def test_margin_funding_uses_less_capital_per_share():
    mtf_like = Funding("test", own_share=0.5)
    spec = SizingInput(1_000_000, ONE_PCT, 500, 495, charges=NO_CHARGES, funding=mtf_like)
    result = size_position(spec)
    assert result.allowed[Limit.ALLOCATION] == 800
    assert result.capital_used == result.position_value / 2


@pytest.mark.parametrize(
    ("capital", "risk", "entry", "stop"),
    [
        (0, ONE_PCT, 500, 450),
        (1e6, RiskSpec(RiskMode.PERCENT, 0), 500, 450),
        (1e6, ONE_PCT, 500, 500),
    ],
)
def test_unsizeable_inputs_are_rejected(capital, risk, entry, stop):
    with pytest.raises(RiskInputError):
        size_position(SizingInput(capital, risk, entry, stop))


# --- warnings --------------------------------------------------------------


def _sized(entry: float = 500, stop: float = 450, **kwargs) -> SizingResult:
    return size_position(SizingInput(1_000_000, ONE_PCT, entry, stop, charges=NO_CHARGES, **kwargs))


def _messages(result, **context) -> list[str]:
    return [w.message for w in trade_warnings(result, TradeContext(**context))]


def test_stop_wider_than_the_band_warns_about_locked_sessions():
    messages = _messages(_sized(stop=460), band=PriceBand.P5, avg_volume=1e6)
    assert any("8.0% away" in m and "2 locked sessions" in m for m in messages)
    within_band = _messages(_sized(stop=480), band=PriceBand.P5, avg_volume=1e6)
    assert not any("price band" in m for m in within_band)
    assert not any("price band" in m for m in _messages(_sized(stop=460), band=PriceBand.NO_BAND))


def test_surveillance_series_and_liquidity():
    result = _sized()  # 200 shares
    messages = _messages(result, surveillance=Surveillance(stasm=2), series="BE", avg_volume=10_000)
    assert any("STASM-II" in m for m in messages)
    assert any("Trade-for-trade series BE" in m for m in messages)
    assert any("2.0% of the average daily volume" in m for m in messages)

    danger = trade_warnings(result, TradeContext(avg_volume=2_000))
    assert danger[0].severity is Severity.DANGER  # 10%: most severe first


def test_zero_quantity_says_why():
    result = _sized(heat_left=0)
    warnings = trade_warnings(result, TradeContext(avg_volume=1e6))
    assert warnings[0].severity is Severity.DANGER
    assert "heat limit leaves no risk" in warnings[0].message


# --- portfolio ---------------------------------------------------------------


def _position(entry: float, stop: float, qty: int = 100, **kwargs) -> Position:
    return Position(
        id=1,
        symbol="X",
        instrument_key=None,
        quantity=qty,
        entry=entry,
        stop=stop,
        initial_stop=kwargs.pop("initial_stop", stop),
        opened_on=dt.date(2026, 9, 1),
        **kwargs,
    )


def test_heat_counts_only_risk_below_entry():
    losing = _position(500, 450)  # ₹5,000 at risk
    trailed = _position(200, 210, initial_stop=180)  # stop above entry: no risk
    closed = _position(100, 90, closed_on=dt.date(2026, 9, 10), exit_price=120)
    summary = summarise([losing, trailed, closed], capital=100_000, heat_limit_pct=6)
    assert summary.heat == 5_000 and summary.heat_pct == 5.0
    assert summary.heat_left == 1_000
    assert summary.capital_used == 70_000 and summary.free_capital == 30_000
    assert summary.open_count == 2
    assert summary.realised_pnl == 2_000


def test_position_pnl_giveback_and_r_multiple():
    position = _position(200, 210, initial_stop=180)
    assert position.pnl(230) == 3_000
    assert position.giveback(230) == 2_000
    assert position.r_multiple(240) == 2.0
