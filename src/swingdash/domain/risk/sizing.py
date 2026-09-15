"""
How many shares: the most that keeps every limit.

- risk: the loss if the stop is hit, charges included, stays within the
  per-trade risk (% of capital or a fixed ₹ amount);
- heat: ...and within what the portfolio heat limit still allows;
- allocation: the position uses at most `max_allocation_pct` of capital;
- free capital: it fits in the capital not already in open positions;
- lot size: SME stocks trade in lots, so quantity is a whole number of lots.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from swingdash.domain.risk.charges import UPSTOX_DELIVERY, Charges, ChargeSchedule, round_trip
from swingdash.domain.risk.funding import NORMAL, Funding
from swingdash.domain.risk.stops import RiskInputError


class RiskMode(StrEnum):
    PERCENT = "percent"
    AMOUNT = "amount"


@dataclass(frozen=True)
class RiskSpec:
    mode: RiskMode
    value: float  # % of capital, or ₹

    def amount(self, capital: float) -> float:
        return capital * self.value / 100 if self.mode is RiskMode.PERCENT else self.value


class Limit(StrEnum):
    # Declared in the order they're reported when several bind at once.
    RISK = "risk"
    HEAT = "heat"
    ALLOCATION = "allocation"
    FREE_CAPITAL = "free capital"


@dataclass(frozen=True)
class SizingInput:
    capital: float
    risk: RiskSpec
    entry: float
    stop: float
    max_allocation_pct: float = 20.0
    free_capital: float | None = None  # None: nothing else is invested
    heat_left: float | None = None  # ₹ of risk the heat limit still allows; None: no limit
    lot_size: int = 1
    charges: ChargeSchedule = UPSTOX_DELIVERY
    funding: Funding = NORMAL


@dataclass(frozen=True)
class SizingResult:
    quantity: int
    entry: float
    stop: float
    capital: float
    position_value: float
    capital_used: float
    loss_at_stop: float  # before charges
    charges: Charges  # buying at entry, selling at the stop
    risk_budget: float  # the tightest ₹ risk limit
    limited_by: Limit
    allowed: dict[Limit, int]  # the quantity each limit alone would allow

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop

    @property
    def stop_pct(self) -> float:
        return self.risk_per_share / self.entry * 100

    @property
    def total_risk(self) -> float:
        return self.loss_at_stop + self.charges.total

    @property
    def risk_pct(self) -> float:
        return self.total_risk / self.capital * 100

    @property
    def allocation_pct(self) -> float:
        return self.capital_used / self.capital * 100

    def target(self, r_multiple: float) -> float:
        return self.entry + r_multiple * self.risk_per_share


def size_position(spec: SizingInput) -> SizingResult:
    if spec.capital <= 0:
        raise RiskInputError("Set your capital first.")
    if spec.risk.value <= 0:
        raise RiskInputError("Risk per trade must be above zero.")
    if not 0 < spec.stop < spec.entry:
        raise RiskInputError("The stop must be above zero and below entry.")
    if spec.lot_size < 1:
        raise RiskInputError("Lot size must be at least 1.")

    per_share_risk = spec.entry - spec.stop

    def risk_at(quantity: int) -> float:
        charges = round_trip(spec.charges, quantity, spec.entry, spec.stop)
        return quantity * per_share_risk + charges.total

    lot = spec.lot_size
    risk_budget = spec.risk.amount(spec.capital)
    allowed = {Limit.RISK: _most_lots(risk_budget, risk_at, per_share_risk, lot)}
    if spec.heat_left is not None:
        heat_left = max(0.0, spec.heat_left)
        allowed[Limit.HEAT] = _most_lots(heat_left, risk_at, per_share_risk, lot)
        risk_budget = min(risk_budget, heat_left)
    per_share_capital = spec.funding.capital_required(spec.entry)
    allocation = spec.capital * spec.max_allocation_pct / 100
    allowed[Limit.ALLOCATION] = _whole_lots(allocation / per_share_capital, lot)
    if spec.free_capital is not None:
        free = max(0.0, spec.free_capital)
        allowed[Limit.FREE_CAPITAL] = _whole_lots(free / per_share_capital, lot)

    quantity = min(allowed.values())
    limited_by = next(limit for limit in Limit if allowed.get(limit) == quantity)
    value = quantity * spec.entry
    return SizingResult(
        quantity=quantity,
        entry=spec.entry,
        stop=spec.stop,
        capital=spec.capital,
        position_value=value,
        capital_used=spec.funding.capital_required(value),
        loss_at_stop=quantity * per_share_risk,
        charges=round_trip(spec.charges, quantity, spec.entry, spec.stop),
        risk_budget=risk_budget,
        limited_by=limited_by,
        allowed=allowed,
    )


def _whole_lots(shares: float, lot: int) -> int:
    return max(0, math.floor(shares / lot)) * lot


def _most_lots(budget: float, risk_at: Callable[[int], float], per_share: float, lot: int) -> int:
    """The largest multiple of `lot` whose risk, charges included, fits the budget."""
    low, high = 0, _whole_lots(budget / per_share, lot) // lot  # high: ignoring charges
    while low < high:  # risk only grows with quantity, so bisect
        middle = (low + high + 1) // 2
        if risk_at(middle * lot) <= budget:
            low = middle
        else:
            high = middle - 1
    return low * lot
