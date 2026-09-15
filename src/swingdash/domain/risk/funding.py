"""How a position is paid for. Normal delivery today; MTF adds its own values later."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Funding:
    name: str
    # Share of the position's value paid from your own capital (MTF: the
    # margin; the broker funds the rest and charges interest on it).
    own_share: float = 1.0

    def capital_required(self, position_value: float) -> float:
        return position_value * self.own_share


NORMAL = Funding("normal")
