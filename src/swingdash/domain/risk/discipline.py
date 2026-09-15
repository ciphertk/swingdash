"""
Whether a position follows its plan, and what it really puts at risk.

A position "not followed" has no stop-loss, is still held below its stop,
is bigger than the quantity the Risk tab sized, or has its stop moved below
the planned one. Heat still has to count such positions: with no usable stop,
risk is measured from the current price down to an assumed stop (an ATR
multiple, or a percentage), and flagged as assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swingdash.domain.risk.portfolio import Position

# A stop this close to the planned one isn't "widened" - just rounding.
_STOP_TOLERANCE = 0.005


class Breach(StrEnum):
    NO_STOP = "no SL"
    STOP_BREACHED = "SL hit"
    OVERSIZED = "oversized"
    STOP_WIDENED = "SL wider"


@dataclass(frozen=True)
class AssumedStop:
    """Where to assume the stop is when a position has no usable one."""

    percent: float = 8.0  # below the price; also the fallback without ATR
    atr_multiple: float | None = 1.5  # None: always use `percent`

    def below(self, price: float, atr: float | None) -> float:
        if self.atr_multiple is not None and atr is not None and atr > 0:
            stop = price - self.atr_multiple * atr
        else:
            stop = price * (1 - self.percent / 100)
        return max(0.0, stop)


@dataclass(frozen=True)
class PositionRisk:
    stop: float  # the stop risk is measured to: the position's own, or assumed
    assumed: bool
    amount: float  # ₹ at risk
    breaches: tuple[Breach, ...]


def breaches(position: Position, price: float | None) -> tuple[Breach, ...]:
    """What isn't followed on an open position (nothing for closed ones)."""
    if not position.is_open:
        return ()
    found: list[Breach] = []
    stop = position.stop
    if stop is None:
        found.append(Breach.NO_STOP)
    elif price is not None and price < stop:
        found.append(Breach.STOP_BREACHED)
    if position.planned_quantity is not None and position.quantity > position.planned_quantity:
        found.append(Breach.OVERSIZED)
    if (
        stop is not None
        and position.planned_stop is not None
        and stop < position.planned_stop * (1 - _STOP_TOLERANCE)
    ):
        found.append(Breach.STOP_WIDENED)
    return tuple(found)


def position_risk(
    position: Position, price: float | None, atr: float | None, assumed: AssumedStop
) -> PositionRisk:
    """
    A usable stop keeps entry-based risk: max(0, entry - stop) x quantity - a
    stop trailed above entry risks nothing. No stop, or the price already
    below it: (price - assumed stop below the price) x quantity.
    """
    found = breaches(position, price)
    stop = position.stop
    if stop is not None and (price is None or price >= stop):
        return PositionRisk(stop, False, max(0.0, position.entry - stop) * position.quantity, found)
    basis = price if price is not None else position.entry
    assumed_stop = assumed.below(basis, atr)
    return PositionRisk(assumed_stop, True, (basis - assumed_stop) * position.quantity, found)
