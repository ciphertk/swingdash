"""
Instrument lookup - thin cached reader over the equity/index instrument
masters that scripts/test_connection.py downloads and caches to disk. No
Upstox calls in here; this only reads the JSON files already cached.
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.config import INDEX_CACHE_PATH, INSTRUMENT_CACHE_PATH


@lru_cache(maxsize=1)
def _load_equities() -> list[dict]:
    return json.loads(INSTRUMENT_CACHE_PATH.read_text())


@lru_cache(maxsize=1)
def _load_indices() -> list[dict]:
    return json.loads(INDEX_CACHE_PATH.read_text())


@lru_cache(maxsize=1)
def _equity_symbol_to_key() -> dict[str, str]:
    return {inst["trading_symbol"]: inst["instrument_key"] for inst in _load_equities()}


@lru_cache(maxsize=1)
def _equity_symbol_to_isin() -> dict[str, str]:
    return {inst["trading_symbol"]: inst["isin"] for inst in _load_equities() if inst.get("isin")}


def find_instrument_key(trading_symbol: str) -> str | None:
    """Case-insensitive lookup by NSE trading symbol, e.g. 'reliance' -> 'NSE_EQ|INE002A01018'."""
    return _equity_symbol_to_key().get(trading_symbol.strip().upper())


def find_isin(trading_symbol: str) -> str | None:
    """Case-insensitive lookup by NSE trading symbol - the Fundamentals
    API is keyed by ISIN, not instrument_key."""
    return _equity_symbol_to_isin().get(trading_symbol.strip().upper())


def list_equities() -> list[dict]:
    """Full cached equity instrument list - small enough to filter in memory
    for symbol search (see CLAUDE.md's scalability notes)."""
    return _load_equities()


def list_indices() -> list[dict]:
    """Cached NSE index list - populates the 'change benchmark index'
    dropdown once that UI exists."""
    return _load_indices()
