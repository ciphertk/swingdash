"""Download of Upstox's public NSE instrument master (no token needed)."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class InstrumentMasters:
    equities: list[dict[str, str]]
    indices: list[dict[str, str]]


def download_instrument_masters(url: str, timeout: float = 30) -> InstrumentMasters:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    instruments: list[dict[str, Any]] = json.loads(gzip.decompress(response.content))

    equities = [
        {
            "instrument_key": inst["instrument_key"],
            "trading_symbol": inst["trading_symbol"],
            "name": inst.get("name", ""),
            "isin": inst.get("isin", ""),
        }
        for inst in instruments
        if inst.get("instrument_type") == "EQ" and inst.get("segment") == "NSE_EQ"
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
