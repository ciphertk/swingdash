"""
Reference data for the NSE market: EQ-series stocks, ETFs and indices, with
each security's price band and exchange surveillance flags.

This is end-of-day data - it changes at most once a trading day - so it is
fetched on demand and stored, never streamed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, fields, replace
from enum import Enum

# NSE's rolling-settlement equity series. BE (trade-to-trade), SM/ST (SME)
# and the rest are deliberately out of scope for the stock list.
EQ_SERIES = "EQ"

_ROMAN = ("0", "I", "II", "III", "IV", "V", "VI")


class PriceBand(Enum):
    """Daily price band (circuit filter). NO_BAND: F&O stocks, which have none."""

    P2 = "2"
    P5 = "5"
    P10 = "10"
    P20 = "20"
    P40 = "40"
    NO_BAND = "NB"

    @property
    def label(self) -> str:
        return "NB" if self is PriceBand.NO_BAND else f"{self.value}%"

    @classmethod
    def parse(cls, text: str) -> PriceBand | None:
        value = text.strip()
        if value.lower() == "no band":
            return cls.NO_BAND
        try:
            return cls(value)
        except ValueError:
            return None


def roman(stage: int) -> str:
    return _ROMAN[stage] if 0 <= stage < len(_ROMAN) else str(stage)


@dataclass(frozen=True)
class Surveillance:
    """
    Exchange surveillance stages; None means the measure doesn't apply.

    gsm: Graded Surveillance Measure stage 0-VI. esm: Enhanced Surveillance
    Measure I-II. ltasm / stasm: long / short term Additional Surveillance
    Measure. ibc: insolvency flag - 0 for a receipt-of-disclosure listing,
    otherwise the ASM IBC stage.
    """

    gsm: int | None = None
    esm: int | None = None
    ltasm: int | None = None
    stasm: int | None = None
    ibc: int | None = None

    @property
    def flagged(self) -> bool:
        return any(getattr(self, f.name) is not None for f in fields(self))

    def labels(self) -> tuple[str, ...]:
        """Short display labels, most severe measure first, e.g. ("GSM-0", "LTASM-I")."""
        labels: list[str] = []
        for name, value in (
            ("GSM", self.gsm),
            ("ESM", self.esm),
            ("LTASM", self.ltasm),
            ("STASM", self.stasm),
        ):
            if value is not None:
                labels.append(f"{name}-{roman(value)}")
        if self.ibc is not None:
            labels.append("IBC" if self.ibc == 0 else f"IBC-{roman(self.ibc)}")
        return tuple(labels)

    def merge(self, other: Surveillance) -> Surveillance:
        """Fill this record's gaps from `other` - one security can appear in several lists."""
        return replace(
            self,
            **{
                f.name: getattr(other, f.name)
                for f in fields(self)
                if getattr(self, f.name) is None
            },
        )


NOT_UNDER_SURVEILLANCE = Surveillance()


@dataclass(frozen=True)
class ListedEquity:
    """A row of NSE's equity list: identity and listing details."""

    symbol: str
    name: str
    isin: str | None
    listed_on: dt.date | None


@dataclass(frozen=True)
class BandEntry:
    symbol: str
    name: str
    band: PriceBand | None


@dataclass(frozen=True)
class Equity:
    symbol: str
    name: str
    isin: str | None
    listed_on: dt.date | None
    band: PriceBand | None
    surveillance: Surveillance = NOT_UNDER_SURVEILLANCE
    sector: str | None = None
    market_cap_cr: float | None = None


@dataclass(frozen=True)
class Etf:
    symbol: str
    name: str
    underlying: str
    asset_class: str  # EQUITY | DEBT | COMMODITY | ...
    isin: str | None
    listed_on: dt.date | None
    band: PriceBand | None = None


@dataclass(frozen=True)
class IndexRow:
    name: str
    category: str
    last: float | None
    change_pct: float | None
    pe: float | None
    pb: float | None
    dividend_yield: float | None
    year_high: float | None
    year_low: float | None
    advances: int | None
    declines: int | None


@dataclass(frozen=True)
class Fetched[T]:
    """A dataset as delivered by its source, with the trading date it describes."""

    rows: T
    as_of: dt.date | None


@dataclass(frozen=True)
class DatasetStatus:
    name: str
    as_of: dt.date | None
    fetched_at: dt.datetime | None
    rows: int
    error: str | None = None


@dataclass(frozen=True)
class SecuritiesSnapshot:
    equities: tuple[Equity, ...]
    etfs: tuple[Etf, ...]
    indices: tuple[IndexRow, ...]
    datasets: dict[str, DatasetStatus]
    # Bumped on every rebuild, so a view can skip redrawing unchanged data.
    version: int

    @property
    def empty(self) -> bool:
        return not (self.equities or self.etfs or self.indices)


EMPTY_SNAPSHOT = SecuritiesSnapshot((), (), (), {}, 0)
