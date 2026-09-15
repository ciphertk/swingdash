"""What to double-check before taking a sized trade."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from swingdash.domain.risk.sizing import Limit, SizingResult
from swingdash.domain.securities import NOT_UNDER_SURVEILLANCE, PriceBand, Surveillance

# Above this share of the average daily volume, getting out at the stop is
# itself a risk.
LIQUIDITY_DANGER_PCT = 5.0
# Series that settle trade-for-trade: every trade is delivered, no netting.
_TRADE_FOR_TRADE = {"BE": "BE", "BZ": "BZ (Z category)", "ST": "ST (SME)"}


class Severity(IntEnum):
    INFO = 1
    WARN = 2
    DANGER = 3


@dataclass(frozen=True)
class RiskWarning:
    severity: Severity
    message: str


@dataclass(frozen=True)
class TradeContext:
    band: PriceBand | None = None  # None: not known (Securities data not fetched yet)
    surveillance: Surveillance = NOT_UNDER_SURVEILLANCE
    series: str | None = None  # NSE series, e.g. EQ / BE / SM
    avg_volume: float | None = None  # average daily volume over recent sessions
    liquidity_warn_pct: float = 1.0


def trade_warnings(result: SizingResult, context: TradeContext) -> list[RiskWarning]:
    """Most severe first."""
    found: list[RiskWarning] = []
    found.extend(_limit_warnings(result))
    found.extend(_band_warnings(result, context.band))
    if context.surveillance.flagged:
        labels = ", ".join(context.surveillance.labels())
        found.append(
            RiskWarning(
                Severity.WARN,
                f"Under surveillance ({labels}): higher margins, possibly trade-for-trade.",
            )
        )
    series = (context.series or "").upper()
    if series in _TRADE_FOR_TRADE:
        found.append(
            RiskWarning(
                Severity.WARN,
                f"Trade-for-trade series {_TRADE_FOR_TRADE[series]}: delivery only, "
                "no same-day exit.",
            )
        )
    if result.quantity > 0:
        found.extend(_liquidity_warnings(result, context))
    return sorted(found, key=lambda w: w.severity, reverse=True)


def _limit_warnings(result: SizingResult) -> list[RiskWarning]:
    if result.quantity == 0:
        reason = {
            Limit.RISK: "one share (or lot) risks more than the per-trade risk",
            Limit.HEAT: "the portfolio heat limit leaves no risk for another trade",
            Limit.ALLOCATION: "one share (or lot) costs more than the allocation cap",
            Limit.FREE_CAPITAL: "there's no free capital left",
        }[result.limited_by]
        return [RiskWarning(Severity.DANGER, f"No position possible: {reason}.")]
    if result.limited_by is Limit.HEAT:
        return [
            RiskWarning(
                Severity.WARN,
                f"Reduced by the heat limit: ₹{result.risk_budget:,.0f} of risk left "
                f"(the per-trade risk alone allows {result.allowed[Limit.RISK]:,}).",
            )
        ]
    if result.limited_by in (Limit.ALLOCATION, Limit.FREE_CAPITAL):
        return [
            RiskWarning(
                Severity.INFO,
                f"Capped by {result.limited_by.value}; the per-trade risk alone allows "
                f"{result.allowed[Limit.RISK]:,}.",
            )
        ]
    return []


def _band_warnings(result: SizingResult, band: PriceBand | None) -> list[RiskWarning]:
    if band is None or band is PriceBand.NO_BAND:
        return []
    band_pct = float(band.value)
    if result.stop_pct > band_pct:
        sessions = math.ceil(result.stop_pct / band_pct)
        return [
            RiskWarning(
                Severity.WARN,
                f"Stop is {result.stop_pct:.1f}% away but the price band is {band.label}: "
                f"reaching it can take {sessions} locked sessions, and the exit may be "
                "well below the stop.",
            )
        ]
    if band is PriceBand.P2:
        return [RiskWarning(Severity.WARN, "2% band: lower circuits can lock you in.")]
    return []


def _liquidity_warnings(result: SizingResult, context: TradeContext) -> list[RiskWarning]:
    if context.avg_volume is None or context.avg_volume <= 0:
        return [RiskWarning(Severity.INFO, "No volume history to check liquidity against.")]
    share = result.quantity / context.avg_volume * 100
    if share >= LIQUIDITY_DANGER_PCT:
        return [
            RiskWarning(
                Severity.DANGER,
                f"{share:.1f}% of the average daily volume - hard to exit at the stop.",
            )
        ]
    if share >= context.liquidity_warn_pct:
        return [RiskWarning(Severity.WARN, f"{share:.1f}% of the average daily volume.")]
    return []
