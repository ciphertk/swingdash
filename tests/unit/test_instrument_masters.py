"""Which rows of Upstox's NSE instrument master count as stocks."""

from swingdash.adapters.upstox.instruments import parse_instrument_masters


def _row(symbol: str, series: str, segment: str = "NSE_EQ") -> dict[str, str]:
    return {
        "segment": segment,
        "instrument_type": series,
        "trading_symbol": symbol,
        "instrument_key": f"{segment}|{symbol}",
        "name": symbol.title(),
        "isin": f"INE{symbol}",
    }


def test_every_equity_series_is_kept():
    # Real examples (Sep 2026): HFCL and MTARTECH were in BE (trade-for-trade)
    # and dropped by an EQ-only filter, so watchlists silently lost them.
    masters = parse_instrument_masters(
        [
            _row("RELIANCE", "EQ"),
            _row("HFCL", "BE"),
            _row("MTARTECH", "BE"),
            _row("HDIL", "BZ"),
            _row("APRAMEYA", "SM"),
            _row("KCK", "ST"),
        ]
    )
    assert [e["trading_symbol"] for e in masters.equities] == [
        "RELIANCE",
        "HFCL",
        "MTARTECH",
        "HDIL",
        "APRAMEYA",
        "KCK",
    ]
    assert masters.equities[1] == {
        "instrument_key": "NSE_EQ|HFCL",
        "trading_symbol": "HFCL",
        "name": "Hfcl",
        "isin": "INEHFCL",
    }


def test_bonds_reits_invits_and_other_segments_are_not_stocks():
    masters = parse_instrument_masters(
        [
            _row("883GS2041", "GS"),
            _row("SGBJUN31", "SG"),
            _row("IRFC-N1", "N1"),
            _row("MINDSPACE", "RR"),
            _row("IRBINVIT", "IV"),
            _row("NIFTY 50", "INDEX", segment="NSE_INDEX"),
            _row("RELIANCE", "EQ", segment="BSE_EQ"),
        ]
    )
    assert masters.equities == []
    assert [i["trading_symbol"] for i in masters.indices] == ["NIFTY 50"]
