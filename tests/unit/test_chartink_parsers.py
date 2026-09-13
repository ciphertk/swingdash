"""Chartink parsers against trimmed real captures (13 Sep 2026)."""

import datetime as dt
import json
from pathlib import Path

import pytest

from swingdash.adapters.chartink.http import ChartinkFormatError, csrf_token
from swingdash.adapters.chartink.parsers import (
    parse_dashboard_page,
    parse_screener_page,
    parse_screener_response,
    parse_widget_response,
)
from swingdash.domain.calendar import IST

FIXTURES = Path(__file__).parents[1] / "fixtures" / "chartink"


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_screener_rows_keep_every_data_column_in_order():
    result = parse_screener_response(_json("screener_process.json"))
    assert result.columns == ("nsecode", "name", "bsecode", "close", "per_chg", "volume")
    assert result.is_stock_list
    first = result.rows[0]
    assert first.key == "LT"
    assert first.values["close"] == 3930.7
    assert first.values["name"] == "Larsen & Toubro Limited"
    assert "sr" not in first.values


def test_screener_custom_columns_become_columns():
    payload = {"data": [{"sr": 1, "nsecode": "TBZ", "name": "TBZ", "mswing": 8.93, "rvol%": 212}]}
    result = parse_screener_response(payload)
    assert result.columns == ("nsecode", "name", "mswing", "rvol%")
    assert result.rows[0].values["rvol%"] == 212


def test_a_bse_only_row_is_keyed_by_its_bse_code():
    result = parse_screener_response({"data": [{"nsecode": "", "bsecode": "500010", "name": "X"}]})
    assert result.rows[0].key == "500010"


def test_table_widget_takes_the_latest_value_of_each_column():
    result = parse_widget_response(_json("widget_table.json"))
    assert result.group_by == "symbol"
    assert result.columns == ("difference%", "industry", "difference")
    assert result.rows[0].key == "LAURUSLABS"
    assert result.rows[0].values["industry"] == "pharmaceuticals - indian - bulk drugs & formln"
    assert result.available == 1301
    assert result.data_time == dt.datetime.fromtimestamp(1789120740, IST)


def test_a_sector_widget_is_not_a_stock_list():
    result = parse_widget_response(_json("widget_sector.json"))
    assert result.group_by == "sector"
    assert not result.is_stock_list
    assert result.rows[1].key == "aerospace & defence"
    assert result.rows[1].values["advancespercentage"] == pytest.approx(61.7647)


def test_an_aggregate_widget_has_one_row_and_no_grouping():
    result = parse_widget_response(_json("widget_nogroups.json"))
    assert result.group_by is None
    assert [(r.key, r.values) for r in result.rows] == [("*no-groups*", {"%": 13.2075})]


def test_a_series_is_reduced_to_its_last_value():
    payload = {
        "metaData": [{"columnAliases": ["close"], "groups": ["symbol"]}],
        "groupData": [{"name": "TCS", "results": [{"close": [2400.0, 2410.5, 2438.2]}]}],
    }
    assert parse_widget_response(payload).rows[0].values == {"close": 2438.2}


@pytest.mark.parametrize("payload", [{}, {"data": "nope"}, None, ["x"]])
def test_unexpected_screener_shapes_fail_loudly(payload):
    with pytest.raises(ChartinkFormatError):
        parse_screener_response(payload)


@pytest.mark.parametrize("payload", [{}, {"metaData": []}, {"metaData": [{}], "groupData": []}])
def test_unexpected_widget_shapes_fail_loudly(payload):
    with pytest.raises(ChartinkFormatError):
        parse_widget_response(payload)


def test_dashboard_page_reads_its_own_widgets_not_the_starter_templates():
    dashboard = parse_dashboard_page(_text("dashboard_page.html"))
    assert (dashboard.id, dashboard.name, dashboard.is_private) == (
        130216,
        "Swing trade Dashboard",
        False,
    )
    assert [(w.name, w.result_type) for w in dashboard.widgets] == [
        ("Stocks near 52 week high", "table"),
        ("Price EMA SMA 50 Days", "table"),
        ("Weekly Sector Advances %", "barchart"),
        ("Broad Market View CCI34", "areachart"),
    ]
    request = dashboard.widgets[0].request()
    assert request.fields["size"] == "1" and request.fields["query"].startswith("select ")


def test_a_page_without_a_dashboard_explains_privacy():
    with pytest.raises(ChartinkFormatError, match="private"):
        parse_dashboard_page("<html><body>Please log in</body></html>")


def test_screener_page_carries_the_ready_scan_clause():
    screener = parse_screener_page(_text("screener_page.html"))
    assert screener.name == "consolidatedBO"
    assert screener.slug == "consolidatedbo"
    assert not screener.is_private
    assert screener.clause is not None and screener.clause.startswith("( {57960} ( ( {57960}")
    assert screener.request().fields == {"scan_clause": screener.clause}


def test_csrf_token_is_read_from_the_page_meta_tag():
    assert csrf_token(_text("screener_page.html")) == "fixture-token"
    assert csrf_token("<html></html>") is None
