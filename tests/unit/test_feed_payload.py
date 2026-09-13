"""Feed payload parsing, against message shapes captured from Upstox (Sep 2026)."""

from swingdash.adapters.upstox.feed import UpstoxFeedTransport

EQUITY = {
    "fullFeed": {
        "marketFF": {
            "ltpc": {"ltp": 1257.5, "ltt": "1789122509222", "ltq": "42", "cp": 1274.0},
            "vtt": "8777736",
        }
    },
    "requestMode": "full_d5",
}
INDEX = {
    "fullFeed": {
        "indexFF": {
            "ltpc": {"ltp": 21226.95, "ltt": "1789122600000", "cp": 21298.7},
            "marketOHLC": {"ohlc": [{"interval": "1d", "close": 21226.95}]},
        }
    },
    "requestMode": "full_d5",
}


def _ticks(message: dict) -> list[tuple]:
    ticks: list[tuple] = []
    transport = UpstoxFeedTransport(client=None, on_tick=lambda *args: ticks.append(args))  # type: ignore[arg-type]
    transport._handle_message(message)
    return ticks


def test_equity_ticks_carry_volume_and_prices():
    assert _ticks({"feeds": {"NSE_EQ|INE002A01018": EQUITY}}) == [
        ("NSE_EQ|INE002A01018", 8777736, 1257.5, 1274.0)
    ]


def test_index_ticks_carry_prices_without_volume():
    assert _ticks({"feeds": {"NSE_INDEX|NIFTY MIDSML 400": INDEX}}) == [
        ("NSE_INDEX|NIFTY MIDSML 400", None, 21226.95, 21298.7)
    ]


def test_market_info_is_not_a_tick():
    statuses: list[str] = []
    transport = UpstoxFeedTransport(
        client=None,  # type: ignore[arg-type]
        on_tick=lambda *args: None,
        on_status=statuses.append,
    )
    transport._handle_message(
        {"type": "market_info", "marketInfo": {"segmentStatus": {"NSE_EQ": "NORMAL_CLOSE"}}}
    )
    assert statuses == ["NORMAL_CLOSE"]
