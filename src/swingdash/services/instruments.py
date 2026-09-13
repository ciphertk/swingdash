"""
Instrument lookup over the cached NSE instrument masters.

The ~2,600-symbol equity list is small enough to hold and filter in memory
(see CLAUDE.md's scalability notes). It's downloaded by `swingdash setup`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class InstrumentsUnavailableError(RuntimeError):
    pass


class InstrumentService:
    def __init__(self, equities_path: Path, indices_path: Path) -> None:
        self._equities_path = equities_path
        self._indices_path = indices_path
        self._lock = threading.Lock()
        self._equities: list[dict[str, str]] | None = None
        self._indices: list[dict[str, str]] | None = None
        self._symbol_to_key: dict[str, str] = {}
        self._symbol_to_isin: dict[str, str] = {}

    def available(self) -> bool:
        return self._equities_path.is_file()

    def find_instrument_key(self, trading_symbol: str) -> str | None:
        """Case-insensitive, e.g. 'reliance' -> 'NSE_EQ|INE002A01018'."""
        self._ensure_loaded()
        return self._symbol_to_key.get(trading_symbol.strip().upper())

    def find_isin(self, trading_symbol: str) -> str | None:
        """The Fundamentals API is keyed by ISIN, not instrument key."""
        self._ensure_loaded()
        return self._symbol_to_isin.get(trading_symbol.strip().upper())

    def list_equities(self) -> list[dict[str, str]]:
        self._ensure_loaded()
        return list(self._equities or [])

    def list_indices(self) -> list[dict[str, str]]:
        self._ensure_loaded()
        return list(self._indices or [])

    def index_name(self, instrument_key: str) -> str:
        """Display name for an index key, e.g. 'NSE_INDEX|NIFTY MIDSML 400' -> 'NIFTY MIDSML 400'."""
        try:
            for index in self.list_indices():
                if index["instrument_key"] == instrument_key:
                    return index.get("trading_symbol") or index.get("name") or instrument_key
        except InstrumentsUnavailableError:
            pass
        return instrument_key.partition("|")[2] or instrument_key

    def store(self, equities: list[dict[str, str]], indices: list[dict[str, str]]) -> None:
        """Replace the cached masters (written atomically) and reload."""
        for path, rows in ((self._equities_path, equities), (self._indices_path, indices)):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            tmp.replace(path)
        with self._lock:
            self._equities = None
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._equities is not None:
            return
        with self._lock:
            if self._equities is not None:
                return
            if not self.available():
                raise InstrumentsUnavailableError(
                    "NSE instrument list is not downloaded yet - run `swingdash setup`."
                )
            equities: list[dict[str, str]] = _read_json(self._equities_path)
            indices: list[dict[str, str]] = (
                _read_json(self._indices_path) if self._indices_path.is_file() else []
            )
            self._symbol_to_key = {e["trading_symbol"]: e["instrument_key"] for e in equities}
            self._symbol_to_isin = {
                e["trading_symbol"]: e["isin"] for e in equities if e.get("isin")
            }
            self._indices = indices
            self._equities = equities


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
