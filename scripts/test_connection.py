"""
Step 1: prove the Upstox config works end to end.

Run with:
    python scripts/test_connection.py

What it does:
  1. Calls a market-data endpoint (LTP quote for RELIANCE) to confirm the
     Analytics Token is valid and wired up correctly. Deliberately NOT
     using /v2/user/profile or any other Account/Funds/Portfolio API here -
     those require Static IP to be enabled on your account and will 401
     with UDAPI1221 otherwise. Market data APIs work with the Analytics
     Token from any machine, no Static IP needed.
  2. Downloads Upstox's public NSE instrument master (no auth needed for
     this file) and filters it down to tradable NSE equities.
  3. Caches that filtered list to data/nse_equity_instruments.json so the
     rest of the app (watchlist config, symbol -> instrument_key lookup)
     has something to read from without re-downloading every time.

If this script runs clean, your token and network path are both good and
you're ready to build the ingestion service on top of it.
"""
import gzip
import json
import sys
from pathlib import Path

import requests
from upstox_client.rest import ApiException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import INDEX_CACHE_PATH, INSTRUMENT_CACHE_PATH, NSE_INSTRUMENTS_URL
from app.upstox_client_wrapper import market_quote_v3_api

# RELIANCE's instrument key - stable, well-known, good for a smoke test.
_TEST_INSTRUMENT_KEY = "NSE_EQ|INE002A01018"


def check_token() -> None:
    print(f"1. Verifying Analytics Token against GET /v3/market-quote/ltp ...")
    try:
        response = market_quote_v3_api().get_ltp(instrument_key=_TEST_INSTRUMENT_KEY)
    except ApiException as e:
        print("   FAILED - the API rejected the token.")
        print(f"   Status: {e.status}, body: {e.body}")
        sys.exit(1)
    except Exception as e:
        print(f"   FAILED - unexpected error: {e}")
        sys.exit(1)

    print(f"   OK - token is valid. Sample response: {response.data}")


def download_all_instruments() -> list[dict]:
    print("\n2. Downloading NSE instrument master (public, unauthenticated) ...")
    resp = requests.get(NSE_INSTRUMENTS_URL, timeout=30)
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    instruments = json.loads(raw)
    print(f"   Downloaded {len(instruments)} total NSE instruments (all segments).")
    return instruments


def filter_equities(instruments: list[dict]) -> list[dict]:
    equities = [
        inst
        for inst in instruments
        if inst.get("instrument_type") == "EQ" and inst.get("segment") == "NSE_EQ"
    ]
    print(f"   Filtered to {len(equities)} tradable NSE equities (segment=NSE_EQ).")
    return equities


def filter_indices(instruments: list[dict]) -> list[dict]:
    indices = [inst for inst in instruments if inst.get("segment") == "NSE_INDEX"]
    print(f"   Filtered to {len(indices)} NSE indices (segment=NSE_INDEX).")
    return indices


def cache_equities(equities: list[dict]) -> None:
    INSTRUMENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    slim = [
        {
            "instrument_key": inst["instrument_key"],
            "trading_symbol": inst["trading_symbol"],
            "name": inst.get("name", ""),
            "isin": inst.get("isin", ""),
        }
        for inst in equities
    ]
    INSTRUMENT_CACHE_PATH.write_text(json.dumps(slim, indent=2))
    print(f"\n3. Cached {len(slim)} equities to {INSTRUMENT_CACHE_PATH}")


def cache_indices(indices: list[dict]) -> None:
    INDEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    slim = [
        {
            "instrument_key": inst["instrument_key"],
            "trading_symbol": inst["trading_symbol"],
            "name": inst.get("name", ""),
        }
        for inst in indices
    ]
    INDEX_CACHE_PATH.write_text(json.dumps(slim, indent=2))
    print(f"4. Cached {len(slim)} indices to {INDEX_CACHE_PATH}")


def main() -> None:
    check_token()
    instruments = download_all_instruments()
    equities = filter_equities(instruments)
    indices = filter_indices(instruments)
    cache_equities(equities)
    cache_indices(indices)

    print("\nSample entries:")
    for row in equities[:5]:
        print(f"   {row['trading_symbol']:<15} {row['instrument_key']}")

    print("\nAll good. Config is working end to end.")


if __name__ == "__main__":
    main()
