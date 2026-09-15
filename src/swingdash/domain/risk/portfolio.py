"""Positions, open and closed, and what the open ones put at risk (portfolio heat)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from swingdash.domain.risk.discipline import AssumedStop, Breach, PositionRisk, position_risk

MANUAL = "manual"


@dataclass(frozen=True)
class Position:
    id: int
    symbol: str
    instrument_key: str | None
    quantity: int
    entry: float
    stop: float | None  # None: no stop-loss
    initial_stop: float | None  # the stop when the trade was taken (for R multiples)
    opened_on: dt.date
    funding: str = "normal"
    note: str = ""
    closed_on: dt.date | None = None
    exit_price: float | None = None
    source: str = MANUAL  # "manual", or the broker it was imported from ("dhan")
    broker_ref: str | None = None  # stable id of an imported row
    # What the Risk tab sized before the trade, when it did.
    planned_quantity: int | None = None
    planned_stop: float | None = None
    charges: float = 0.0  # actual charges, when the broker reported them

    @property
    def is_open(self) -> bool:
        return self.closed_on is None

    @property
    def is_imported(self) -> bool:
        return self.source != MANUAL

    @property
    def capital_used(self) -> float:
        return self.entry * self.quantity

    @property
    def stop_risk(self) -> float | None:
        """What the stop can still lose from entry; None without a stop."""
        return None if self.stop is None else max(0.0, self.entry - self.stop) * self.quantity

    def pnl(self, price: float) -> float:
        return (price - self.entry) * self.quantity

    def giveback(self, price: float) -> float | None:
        """What a fall from `price` to the stop would take back (negative: below it)."""
        return None if self.stop is None else (price - self.stop) * self.quantity

    def r_multiple(self, price: float) -> float | None:
        """The move from entry in multiples of the risk planned at entry."""
        stop = self.initial_stop if self.initial_stop is not None else self.planned_stop
        if stop is None or self.entry - stop <= 0:
            return None
        return (price - self.entry) / (self.entry - stop)

    @property
    def realised_pnl(self) -> float | None:
        """Before charges."""
        return None if self.exit_price is None else self.pnl(self.exit_price)

    @property
    def net_realised_pnl(self) -> float | None:
        gross = self.realised_pnl
        return None if gross is None else gross - self.charges


@dataclass(frozen=True)
class PortfolioSummary:
    capital: float
    open_count: int
    capital_used: float  # open positions at their entry prices
    current_value: float  # ... at the latest prices (entry where none)
    heat: float  # ₹ at risk to the stops, assumed stops included
    assumed_heat: float  # the part of `heat` measured to assumed stops
    heat_limit_pct: float
    realised_pnl: float  # closed positions, before charges
    charges: float  # actual charges reported on closed positions
    breaches: Mapping[Breach, int] = field(default_factory=dict[Breach, int])

    @property
    def free_capital(self) -> float:
        return max(0.0, self.capital - self.capital_used)

    @property
    def heat_pct(self) -> float:
        return self._pct(self.heat)

    @property
    def assumed_heat_pct(self) -> float:
        return self._pct(self.assumed_heat)

    @property
    def heat_left(self) -> float:
        return max(0.0, self.capital * self.heat_limit_pct / 100 - self.heat)

    @property
    def not_followed(self) -> int:
        return sum(self.breaches.values())

    def _pct(self, amount: float) -> float:
        return amount / self.capital * 100 if self.capital > 0 else 0.0


def summarise(
    positions: Sequence[Position],
    capital: float,
    heat_limit_pct: float,
    risks: Mapping[int, PositionRisk] | None = None,
    prices: Mapping[int, float] | None = None,
) -> PortfolioSummary:
    """
    `risks` and `prices` are per position id; a position missing from `risks`
    is measured to its stop, or to a default assumed stop without one.
    """
    prices = prices or {}
    open_positions = [p for p in positions if p.is_open]
    heat = assumed_heat = current_value = 0.0
    breaches: dict[Breach, int] = {}
    for position in open_positions:
        price = prices.get(position.id)
        risk = (risks or {}).get(position.id) or position_risk(position, price, None, AssumedStop())
        heat += risk.amount
        if risk.assumed:
            assumed_heat += risk.amount
        for breach in risk.breaches:
            breaches[breach] = breaches.get(breach, 0) + 1
        current_value += position.quantity * (price if price is not None else position.entry)
    closed = [p for p in positions if not p.is_open]
    return PortfolioSummary(
        capital=capital,
        open_count=len(open_positions),
        capital_used=sum(p.capital_used for p in open_positions),
        current_value=current_value,
        heat=heat,
        assumed_heat=assumed_heat,
        heat_limit_pct=heat_limit_pct,
        realised_pnl=sum(p.realised_pnl or 0.0 for p in closed),
        charges=sum(p.charges for p in closed),
        breaches=breaches,
    )
