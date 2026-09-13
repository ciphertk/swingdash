"""
Central config. Everything that varies between your machine and anyone
else's (tokens, cache paths) comes from environment variables loaded
from .env - never hardcode the token in code you might commit.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# src/swingdash/config.py -> repo root. Temporary: runtime data moves to the
# OS user data directory once settings are introduced.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

UPSTOX_ANALYTICS_TOKEN = os.getenv("UPSTOX_ANALYTICS_TOKEN", "").strip()

INSTRUMENT_CACHE_PATH = PROJECT_ROOT / os.getenv(
    "INSTRUMENT_CACHE_PATH", "data/nse_equity_instruments.json"
)

# NSE_INDEX segment entries (Nifty 50, Nifty Midcap 150, etc.) cached the
# same way as the equity master - lets a future "change benchmark index"
# UI list real options instead of a hardcoded single choice.
INDEX_CACHE_PATH = PROJECT_ROOT / os.getenv("INDEX_CACHE_PATH", "data/nse_index_instruments.json")

WATCHLIST_PATH = PROJECT_ROOT / "data" / "watchlist.json"

# SQLite - the candle cache (completed daily candles don't change once a
# trading day closes, so they're cached locally instead of re-fetched
# from Upstox every request) and saved watchlists (multiple named lists)
# both live here. Personal single-user
# tool at this scale, per CLAUDE.md's scalability notes - SQLite is
# enough, no need for a client/server DB.
DB_PATH = PROJECT_ROOT / os.getenv("DB_PATH", "data/swing_dashboard.db")

# Mswing's benchmark index (Pine script used NIFTYMIDSML400) - a plain
# constant with an env override, not something engines/mswing.py hardcodes.
# The engine just takes whatever index_bars it's handed; whichever index
# the dashboard/API actually fetches for a given request (this default, or
# one the user picks from the UI) is an ingestion/API-layer decision.
DEFAULT_MSWING_INDEX_KEY = os.getenv("DEFAULT_MSWING_INDEX_KEY", "NSE_INDEX|NIFTY MIDSML 400")

# Public, unauthenticated instrument master files. Upstox refreshes these
# daily (~6 AM IST). No token needed to fetch these.
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def require_token() -> str:
    if not UPSTOX_ANALYTICS_TOKEN:
        raise RuntimeError(
            "UPSTOX_ANALYTICS_TOKEN is not set. Copy .env.example to "
            ".env and paste your Analytics Token in (generate it from "
            "account.upstox.com -> Developer Apps -> your app -> Analytics tab)."
        )
    return UPSTOX_ANALYTICS_TOKEN
