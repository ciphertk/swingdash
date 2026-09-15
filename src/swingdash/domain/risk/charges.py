"""
Round-trip costs of a delivery trade: buying, then selling at `exit`.

Rates for NSE equity delivery at Upstox, verified 15 Sep 2026 against
https://upstox.com/brokerage-charges/ :
- brokerage ₹20 per executed order;
- STT 0.1% on buy and sell;
- NSE transaction charges 0.00307% on buy and sell (from 1 Mar 2026; IPFT
  is included in them);
- SEBI fees ₹10 per crore;
- stamp duty 0.015% on buy;
- GST 18% on brokerage + transaction charges + DP charges;
- DP charges ₹20 per scrip per day on sell.
Rounding the exchange applies (e.g. STT to the rupee) is ignored - this is
an estimate for sizing, not a contract note.
"""

from __future__ import annotations

from dataclasses import dataclass

_CRORE = 10_000_000


@dataclass(frozen=True)
class ChargeSchedule:
    brokerage_per_order: float = 20.0
    stt_rate: float = 0.001
    exchange_rate: float = 0.0000307
    sebi_per_crore: float = 10.0
    stamp_rate_buy: float = 0.00015
    gst_rate: float = 0.18
    dp_per_sell: float = 20.0


UPSTOX_DELIVERY = ChargeSchedule()
NO_CHARGES = ChargeSchedule(0, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class Charges:
    buy: float
    sell: float

    @property
    def total(self) -> float:
        return self.buy + self.sell


def round_trip(schedule: ChargeSchedule, quantity: int, entry: float, exit: float) -> Charges:
    if quantity <= 0:
        return Charges(0.0, 0.0)
    return Charges(
        _side(schedule, quantity * entry, buying=True),
        _side(schedule, quantity * exit, buying=False),
    )


def _side(s: ChargeSchedule, value: float, *, buying: bool) -> float:
    exchange = s.exchange_rate * value
    dp = 0.0 if buying else s.dp_per_sell
    stamp = s.stamp_rate_buy * value if buying else 0.0
    gst = s.gst_rate * (s.brokerage_per_order + exchange + dp)
    sebi = s.sebi_per_crore * value / _CRORE
    return s.brokerage_per_order + s.stt_rate * value + exchange + sebi + stamp + dp + gst
