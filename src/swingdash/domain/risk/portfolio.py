"""Open positions and the risk they carry (portfolio heat)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    id: int
    symbol: str
    instrument_key: str | None
    quantity: int
    entry: float
    stop: float
    initial_stop: float
    opened_on: dt.date
    funding: str = "normal"
    note: str = ""
    closed_on: dt.date | None = None
    exit_price: float | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_on is None

    @property
    def open_risk(self) -> float:
        """What the stop can still lose from entry - nothing once it's at or above entry."""
        return max(0.0, self.entry - self.stop) * self.quantity

    @property
    def capital_used(self) -> float:
        return self.entry * self.quantity

    def pnl(self, price: float) -> float:
        return (price - self.entry) * self.quantity

    def giveback(self, price: float) -> float:
        """What a fall from `price` to the stop would take back (negative: below the stop)."""
        return (price - self.stop) * self.quantity

    def r_multiple(self, price: float) -> float | None:
        """The move from entry in multiples of the risk taken at entry."""
        per_share = self.entry - self.initial_stop
        return (price - self.entry) / per_share if per_share > 0 else None

    @property
    def realised_pnl(self) -> float | None:
        """Before charges."""
        return None if self.exit_price is None else self.pnl(self.exit_price)


@dataclass(frozen=True)
class PortfolioSummary:
    capital: float
    open_count: int
    capital_used: float
    heat: float  # ₹ the open positions' stops can still lose
    heat_limit_pct: float
    realised_pnl: float  # closed positions, before charges

    @property
    def free_capital(self) -> float:
        return max(0.0, self.capital - self.capital_used)

    @property
    def heat_pct(self) -> float:
        return self.heat / self.capital * 100 if self.capital > 0 else 0.0

    @property
    def heat_left(self) -> float:
        return max(0.0, self.capital * self.heat_limit_pct / 100 - self.heat)


def summarise(
    positions: Sequence[Position], capital: float, heat_limit_pct: float
) -> PortfolioSummary:
    open_positions = [p for p in positions if p.is_open]
    return PortfolioSummary(
        capital=capital,
        open_count=len(open_positions),
        capital_used=sum(p.capital_used for p in open_positions),
        heat=sum(p.open_risk for p in open_positions),
        heat_limit_pct=heat_limit_pct,
        realised_pnl=sum(p.realised_pnl or 0.0 for p in positions if not p.is_open),
    )
