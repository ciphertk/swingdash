import pytest

from swingdash.ui.tradingview import chart_url, open_chart


def test_chart_url_matches_tradingviews_own_link_format():
    assert chart_url("UNIMECH") == "https://in.tradingview.com/chart/?symbol=NSE%3AUNIMECH"


def test_chart_url_encodes_the_colon_for_any_exchange():
    assert chart_url("AAPL", exchange="NASDAQ") == (
        "https://in.tradingview.com/chart/?symbol=NASDAQ%3AAAPL"
    )


def test_open_chart_opens_the_built_url(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    assert open_chart("RELIANCE") is True
    assert opened == ["https://in.tradingview.com/chart/?symbol=NSE%3ARELIANCE"]


def test_open_chart_reports_no_browser(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("webbrowser.open", lambda url: False)
    assert open_chart("RELIANCE") is False
