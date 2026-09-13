"""Pilot tests for the Securities tab, fed by captured NSE files through the fake source."""

from __future__ import annotations

import csv

from textual.widgets import ContentSwitcher, Input

from swingdash.ui.app import SwingDashApp
from swingdash.ui.tabs.rvol.pane import LiveRvolTab
from swingdash.ui.tabs.securities.pane import SecuritiesTab
from swingdash.ui.widgets.nav_table import NavTable
from tests.ui.helpers import until

# Fixture-derived counts: EQ rows in sec_list.csv minus the three ETFs.
FIXTURE_STOCKS = 16


def _tab(app: SwingDashApp) -> SecuritiesTab:
    return app.query_one(SecuritiesTab)


def _table(app: SwingDashApp, view: str = "stocks") -> NavTable:
    return app.query_one(f"#securities-{view}", NavTable)


def _status(app: SwingDashApp) -> str:
    return str(_tab(app)._status_line.render())


def _symbols(table: NavTable) -> list[str]:
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


async def _open_with_data(app: SwingDashApp, pilot) -> None:
    await pilot.press("2")
    await until(pilot, lambda: bool(app.query(SecuritiesTab)))
    await pilot.press("R")
    await until(pilot, lambda: _table(app).row_count > 0)


async def test_tab_mounts_lazily_and_starts_empty(services, securities_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert not app.query(SecuritiesTab)
        await pilot.press("2")
        await until(pilot, lambda: "press R to fetch" in _status(app))
        assert securities_source.calls == []  # manual refresh only
        await pilot.press("1")
        await until(pilot, lambda: app.query_one(LiveRvolTab)._table.row_count == 5)


async def test_refresh_fills_stocks_with_bands_and_surveillance(services, securities_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        table = _table(app)
        await until(pilot, lambda: table.row_count == FIXTURE_STOCKS)
        symbols = _symbols(table)
        assert symbols == sorted(symbols)  # symbol ascending by default
        assert "NIFTYBEES" not in symbols

        raymond = [str(c) for c in table.get_row("RAYMOND")]
        assert raymond[1] == "Raymond Limited"
        assert raymond[4] == "20%"
        assert raymond[5] == "STASM-I"
        assert "bands as of Fri 11 Sep" in _status(app)
        assert set(securities_source.calls) >= {"bands", "surveillance", "etfs", "indices"}


async def test_filters_sort_and_views(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        table = _table(app)
        await until(pilot, lambda: table.row_count == FIXTURE_STOCKS)

        await pilot.press("m")  # surveillance only
        await until(pilot, lambda: table.row_count < FIXTURE_STOCKS)
        assert "RELIANCE" not in _symbols(table) and "RAYMOND" in _symbols(table)
        await pilot.press("m")
        await until(pilot, lambda: table.row_count == FIXTURE_STOCKS)

        await pilot.press("b", "b", "b")  # All -> 2% -> 5% -> 10%
        await until(pilot, lambda: "band 10%" in _status(app))
        assert _symbols(table) and all(str(table.get_row(s)[4]) == "10%" for s in _symbols(table))
        await pilot.press("b", "b", "b")  # 20% -> NB -> All
        await until(pilot, lambda: table.row_count == FIXTURE_STOCKS)

        await pilot.press("r")  # reverse: reorders in place
        await until(pilot, lambda: _symbols(table) == sorted(_symbols(table), reverse=True))
        assert table.row_count == FIXTURE_STOCKS

        await pilot.press("slash", "r", "a", "y", "enter")
        await until(pilot, lambda: _symbols(table) == ["RAYMOND"])

        await pilot.press("v")
        switcher = app.query_one("#securities-switcher", ContentSwitcher)
        assert switcher.current == "securities-indices"
        indices = _table(app, "indices")
        await until(pilot, lambda: indices.row_count == 0)  # the "ray" filter still applies
        await pilot.press("escape")
        await until(pilot, lambda: indices.row_count == 7)

        await pilot.press("v")
        await until(pilot, lambda: _table(app, "etfs").row_count == 3)
        gold = [str(c) for c in _table(app, "etfs").get_row("GOLDBEES")]
        assert gold[1] == "NIPPON INDIA ETF GOLD BEES"


async def test_typing_in_the_filter_never_triggers_shortcuts(services, securities_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        calls = len(securities_source.calls)
        await pilot.press("slash", "R", "v", "q", "1", "b")
        await pilot.pause()
        assert app.query_one("#securities-filter", Input).value == "Rvq1b"
        assert app.is_running
        assert len(securities_source.calls) == calls  # no second refresh
        switcher = app.query_one("#securities-switcher", ContentSwitcher)
        assert switcher.current == "securities-stocks"


async def test_export_writes_the_active_views_filtered_sorted_rows(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        table = _table(app)
        await until(pilot, lambda: table.row_count == FIXTURE_STOCKS)

        await pilot.press("m")  # surveillance only - the export must reflect this
        await until(pilot, lambda: table.row_count < FIXTURE_STOCKS)
        expected_symbols = _symbols(table)

        await pilot.press("x")
        await pilot.pause()

        exports = list(services.settings.paths.exports_dir.glob("securities-stocks_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["SYMBOL", "NAME", "SECTOR", "MCAP CR", "BAND", "SURVEILLANCE", "LISTED"]
        assert [r[0] for r in rows[1:]] == expected_symbols
        raymond = next(r for r in rows[1:] if r[0] == "RAYMOND")
        assert raymond[5] == "STASM-I"  # plain surveillance label, not the styled cell


async def test_export_reflects_the_active_subtab(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        await until(pilot, lambda: _table(app).row_count == FIXTURE_STOCKS)

        await pilot.press("v")  # switch to Indices
        await until(pilot, lambda: _table(app, "indices").row_count == 7)
        await pilot.press("x")
        await pilot.pause()

        exports = list(services.settings.paths.exports_dir.glob("securities-indices_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0][0] == "INDEX"
        assert len(rows) == 8  # header + 7 indices
        assert not list(services.settings.paths.exports_dir.glob("securities-stocks_*.csv"))


async def test_a_failed_dataset_is_reported(services, securities_source):
    securities_source.failing = {"indices"}
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 40)) as pilot:
        await _open_with_data(app, pilot)
        await until(pilot, lambda: "last refresh failed: indices" in _status(app))
        assert _table(app).row_count == FIXTURE_STOCKS  # the rest still loaded
