"""
Phase 1 spike: prove the Analytics Token can open the WebSocket market
data feed, and inspect the exact shape of what comes back.

Run with:
    python scripts/test_feed.py

What it proves:
  1. The Analytics Token authorizes the feed (docs say Websocket needs no
     Static IP, unlike Account/Portfolio APIs - this verifies it rather
     than trusting it).
  2. The payload shape of `full` mode - specifically that `vtt` (volume
     traded today) is present, since that's the RVOL numerator.

Outside market hours you should still see the connection OPEN, which is
the main thing being proven here; ticks only flow 09:15-15:30 IST on a
trading day, though a snapshot of the last session often arrives on
subscribe.
"""

import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import upstox_client

from swingdash.upstox_client_wrapper import get_api_client

# Two liquid names - most likely to tick if the market happens to be open.
_TEST_KEYS = ["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"]  # RELIANCE, TCS
_LISTEN_SECONDS = 20

_state = {"open": False, "messages": 0, "error": None, "first_payload": None}


def _on_open():
    _state["open"] = True
    print("   OPEN - websocket connected and authorized.")


def _on_message(message):
    _state["messages"] += 1
    if _state["first_payload"] is None:
        _state["first_payload"] = message
    if _state["messages"] <= 3:
        print(f"\n   --- message #{_state['messages']} (type={type(message).__name__}) ---")
        _print_feeds(message)


def _on_error(error):
    _state["error"] = error
    print(f"   ERROR: {error}")


def _on_close(*args):
    print(f"   CLOSED {args if args else ''}")


def _print_feeds(message):
    """Pull out the bits that matter for RVOL, without dumping the whole payload."""
    if not isinstance(message, dict):
        print(f"   (non-dict payload, raw repr truncated): {str(message)[:400]}")
        return

    feeds = message.get("feeds")
    if not feeds:
        print(f"   keys={list(message.keys())} (no 'feeds'): {json.dumps(message)[:400]}")
        return

    for key, feed in list(feeds.items())[:2]:
        market_ff = (feed.get("fullFeed") or {}).get("marketFF") or {}
        ltpc = market_ff.get("ltpc") or {}
        ohlc_entries = (market_ff.get("marketOHLC") or {}).get("ohlc") or []
        intervals = sorted({o.get("interval") for o in ohlc_entries if o.get("interval")})
        print(f"   {key}")
        print(f"      vtt (volume traded today) = {market_ff.get('vtt')!r}")
        print(
            f"      ltp={ltpc.get('ltp')!r}  prev_close(cp)={ltpc.get('cp')!r}  atp={market_ff.get('atp')!r}"
        )
        print(f"      marketOHLC intervals present = {intervals} ({len(ohlc_entries)} entries)")
        if not market_ff:
            print(f"      (no marketFF - feed keys: {list(feed.keys())})")


def main() -> None:
    print("1. Opening market data feed with the Analytics Token ...")
    print(f"   subscribing to {_TEST_KEYS} in 'full' mode")

    streamer = upstox_client.MarketDataStreamerV3(
        get_api_client(), _TEST_KEYS, upstox_client.MarketDataStreamerV3.Mode["FULL"]
    )
    streamer.on("open", _on_open)
    streamer.on("message", _on_message)
    streamer.on("error", _on_error)
    streamer.on("close", _on_close)

    # connect() is non-blocking - it spawns ws.run_forever on its own
    # thread, so we poll here rather than the callbacks driving us.
    streamer.connect()

    print(f"\n2. Listening for {_LISTEN_SECONDS}s ...")
    deadline = time.time() + _LISTEN_SECONDS
    while time.time() < deadline:
        time.sleep(0.5)

    print("\n3. Result")
    print(f"   connection opened : {_state['open']}")
    print(f"   messages received : {_state['messages']}")
    print(f"   error             : {_state['error']}")

    if _state["open"] and _state["error"] is None:
        print("\n   PASS - the Analytics Token authorizes the WebSocket feed.")
    else:
        print("\n   FAIL - see error above.")

    with contextlib.suppress(Exception):
        streamer.disconnect()


if __name__ == "__main__":
    main()
