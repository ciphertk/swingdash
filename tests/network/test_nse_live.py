"""
Live checks that NSE's reference files still have the shape the parsers
expect. Deselected by default; run with `uv run pytest -m network`.
"""

import pytest

from swingdash.adapters.nse.http import ARCHIVES
from swingdash.adapters.nse.parsers import parse_surveillance_file
from swingdash.adapters.nse.source import NseSecuritiesSource
from swingdash.domain.calendar import now_ist
from swingdash.domain.securities import PriceBand

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def source():
    nse = NseSecuritiesSource()
    yield nse
    nse.close()


def test_price_bands_and_listing_details(source):
    bands = source.price_bands().rows
    listed = {e.symbol for e in source.equity_list().rows}
    etfs = {e.symbol for e in source.etf_list().rows}
    assert len(bands) > 2000
    assert {b.band for b in bands} >= {PriceBand.P5, PriceBand.P20, PriceBand.NO_BAND}
    stocks = {b.symbol for b in bands} - etfs
    # Nearly every EQ stock has listing details; a handful lag a day behind.
    assert len(stocks - listed) < 50
    assert "RELIANCE" in stocks and "NIFTYBEES" in etfs


def test_indices(source):
    indices = source.indices()
    assert len(indices.rows) > 100
    assert any(r.name == "NIFTY 50" and r.category == "Derivatives" for r in indices.rows)


def test_report_stages_agree_with_the_daily_file(source):
    """Both describe the same measures a day apart, so most listings match."""
    reports = source.surveillance(now_ist().date())
    assert reports.as_of is not None
    reported = {s for s, v in reports.rows.items() if v.ltasm is not None}
    assert len(reported) > 20

    day = reports.as_of
    text, _ = source._http.text(f"{ARCHIVES}/cm/REG1_IND{day:%d%m%y}.csv")
    filed = {s for s, v in parse_surveillance_file(text).items() if v.ltasm is not None}
    overlap = len(reported & filed) / len(reported | filed)
    assert overlap > 0.8
