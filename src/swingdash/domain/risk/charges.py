"""
Round-trip costs of a delivery trade: buying, then selling at `exit`.

NSE equity delivery rates per broker, verified 15 Sep 2026 on each broker's
own pricing page. Common to all three: STT 0.1% on buy and sell, stamp duty
0.015% on buy, SEBI fees ₹10 per crore (Dhan: 0.0001%, the same), GST 18%.

- Upstox (https://upstox.com/brokerage-charges/): brokerage ₹20 per executed
  order; NSE transaction charges 0.00307% (from 1 Mar 2026, IPFT included);
  GST on brokerage + transaction charges + DP charges; DP ₹20 per scrip per
  day on sell, plus GST.
- Dhan (https://dhan.co/pricing/): brokerage ₹0; NSE transaction charges
  0.0030699%; GST on brokerage + transaction charges + SEBI fees (+ IPFT,
  0.0000001% - negligible, left out); DP ₹12.50 per ISIN per sell
  instruction, plus GST.
- Zerodha (https://zerodha.com/charges/): brokerage ₹0; NSE transaction
  charges 0.00307%; GST on brokerage + SEBI fees + transaction charges; DP
  ₹15.34 per scrip on sell, GST already included (₹3.5 CDSL + ₹9.5 Zerodha +
  ₹2.34 GST).

Rounding the exchange applies (e.g. STT to the rupee) is ignored - this is
an estimate for sizing, not a contract note.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

_CRORE = 10_000_000


class Broker(StrEnum):
    UPSTOX = "upstox"
    DHAN = "dhan"
    ZERODHA = "zerodha"


@dataclass(frozen=True)
class ChargeSchedule:
    name: str
    brokerage_per_order: float = 0.0
    stt_rate: float = 0.001
    exchange_rate: float = 0.0000307
    sebi_per_crore: float = 10.0
    stamp_rate_buy: float = 0.00015
    gst_rate: float = 0.18
    gst_on_sebi: bool = False  # brokers differ on whether SEBI fees attract GST
    dp_per_sell: float = 0.0
    dp_includes_gst: bool = False


SCHEDULES: dict[Broker, ChargeSchedule] = {
    Broker.UPSTOX: ChargeSchedule("Upstox", brokerage_per_order=20.0, dp_per_sell=20.0),
    Broker.DHAN: ChargeSchedule(
        "Dhan", exchange_rate=0.000030699, gst_on_sebi=True, dp_per_sell=12.5
    ),
    Broker.ZERODHA: ChargeSchedule(
        "Zerodha", gst_on_sebi=True, dp_per_sell=15.34, dp_includes_gst=True
    ),
}
UPSTOX_DELIVERY = SCHEDULES[Broker.UPSTOX]
NO_CHARGES = ChargeSchedule(
    "none", stt_rate=0, exchange_rate=0, sebi_per_crore=0, stamp_rate_buy=0, gst_rate=0
)


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
    brokerage = s.brokerage_per_order
    exchange = s.exchange_rate * value
    sebi = s.sebi_per_crore * value / _CRORE
    stamp = s.stamp_rate_buy * value if buying else 0.0
    dp = 0.0 if buying else s.dp_per_sell
    taxable = brokerage + exchange + (sebi if s.gst_on_sebi else 0.0)
    if not s.dp_includes_gst:
        taxable += dp
    return brokerage + s.stt_rate * value + exchange + sebi + stamp + dp + s.gst_rate * taxable
