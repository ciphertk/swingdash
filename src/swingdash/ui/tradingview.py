"""
Open a symbol's TradingView chart in the local machine's default browser.

Only meaningful when swingdash runs on the same machine as the browser -
over SSH/remote sessions this opens on the server, not the viewer.
"""

from __future__ import annotations

import webbrowser
from urllib.parse import quote

CHART_URL = "https://in.tradingview.com/chart/?symbol="


def chart_url(symbol: str, exchange: str = "NSE") -> str:
    return CHART_URL + quote(f"{exchange}:{symbol}")


def open_chart(symbol: str, exchange: str = "NSE") -> bool:
    """Open `symbol`'s TradingView chart. True if a browser was launched."""
    return webbrowser.open(chart_url(symbol, exchange))
