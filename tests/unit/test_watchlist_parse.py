import pytest

from swingdash.domain.watchlist import parse_symbols_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NSE:RAYMOND, NSE:GENESYS\nNSE:UNIMECH", ["RAYMOND", "GENESYS", "UNIMECH"]),
        ("NSE:TCS, reliance\nBSE:INFY;WIPRO", ["TCS", "RELIANCE", "INFY", "WIPRO"]),
        ("SBIN AXISBANK  HDFCBANK", ["SBIN", "AXISBANK", "HDFCBANK"]),
        ("TCS,,tcs\n\n  TCS  ,NSE:TCS", ["TCS"]),
        ("", []),
    ],
)
def test_paste_import_normalises_real_world_formats(text, expected):
    assert parse_symbols_text(text) == expected
