"""Download of Upstox's public NSE instrument master (no token needed)."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any

import requests

# Upstox's `instrument_type` for NSE_EQ is the NSE series. A stock's series
# changes as surveillance moves it in and out of trade-for-trade (EQ <-> BE),
# but its instrument key (ISIN-based) doesn't - so every equity series is
# kept, or a stock under surveillance silently can't be looked up.
# EQ normal (ETFs too), BE trade-for-trade, BZ Z-category, SM SME, ST SME
# trade-for-trade. Excluded: bonds/G-secs/NCDs (N*, Y*, Z*, GS, SG, ...),
# REITs (RR) and InvITs (IV).
EQUITY_SERIES = frozenset({"EQ", "BE", "BZ", "SM", "ST"})


@dataclass(frozen=True)
class InstrumentMasters:
    equities: list[dict[str, str]]
    indices: list[dict[str, str]]


def download_instrument_masters(url: str, timeout: float = 30) -> InstrumentMasters:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return parse_instrument_masters(json.loads(gzip.decompress(response.content)))


def parse_instrument_masters(instruments: list[dict[str, Any]]) -> InstrumentMasters:
    equities = [
        {
            "instrument_key": inst["instrument_key"],
            "trading_symbol": inst["trading_symbol"],
            "name": inst.get("name", ""),
            "isin": inst.get("isin", ""),
        }
        for inst in instruments
        if inst.get("segment") == "NSE_EQ" and inst.get("instrument_type") in EQUITY_SERIES
    ]
    indices = [
        {
            "instrument_key": inst["instrument_key"],
            "trading_symbol": inst["trading_symbol"],
            "name": inst.get("name", ""),
        }
        for inst in instruments
        if inst.get("segment") == "NSE_INDEX"
    ]
    return InstrumentMasters(equities=equities, indices=indices)
