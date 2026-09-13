"""Watchlist model and paste-import parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_WATCHLIST_NAME = "default"


@dataclass(frozen=True)
class Watchlist:
    name: str
    symbols: tuple[str, ...]


def parse_symbols_text(text: str) -> list[str]:
    """
    Paste-import parsing. Accepts commas, newlines, semicolons or plain
    spaces as separators, uppercases, and de-duplicates while preserving
    order.

    Exchange prefixes are stripped: watchlists are usually copied out of
    TradingView or a screener, which write "NSE:RAYMOND", while the
    instrument master is keyed on the bare trading symbol. Keeping the
    prefix silently resolves every symbol to nothing and renders an empty
    table. NSE trading symbols never contain spaces or colons, so this is
    unambiguous.
    """
    seen: set[str] = set()
    symbols: list[str] = []
    for token in re.split(r"[,\s;]+", text):
        symbol = token.strip().upper()
        if ":" in symbol:
            symbol = symbol.rsplit(":", 1)[-1]  # NSE:RAYMOND -> RAYMOND
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols
