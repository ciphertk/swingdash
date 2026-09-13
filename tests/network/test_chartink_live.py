"""
Live checks that Chartink still answers the way the adapter expects.
Deselected by default; run with `uv run pytest -m network`. Kept to a
handful of requests - Chartink is a website, not an API.
"""

import pytest

from swingdash.adapters.chartink.source import ChartinkSource
from swingdash.domain.chartink import ChartinkKind, ChartinkRequest

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def source():
    chartink = ChartinkSource()
    yield chartink
    chartink.close()


def test_a_public_screener_imports_by_link_and_runs(source):
    screener = source.screener("https://chartink.com/screener/consolidatedbo")
    assert screener.clause and "{57960}" in screener.clause
    result = source.run(screener.request())
    assert result.is_stock_list
    assert "nsecode" in result.columns and "close" in result.columns


def test_a_public_dashboards_table_widget_runs(source):
    dashboard = source.dashboard("https://chartink.com/dashboard/130216")
    table = next(w for w in dashboard.widgets if w.is_table)
    result = source.run(table.request())
    assert result.group_by == "symbol"
    assert result.rows


def test_a_bare_widget_query_runs(source):
    request = ChartinkRequest(
        ChartinkKind.WIDGET,
        {
            "query": "select latest Close as 'Close' WHERE( {cash} ( symbol = 'reliance' ) ) ORDER BY 1 desc",
            "use_live": "1",
            "limit": "10",
            "size": "1",
        },
    )
    result = source.run(request)
    assert [row.key for row in result.rows] == ["RELIANCE"]
    assert isinstance(result.rows[0].values["close"], int | float)
