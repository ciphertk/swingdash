"""Pilot tests for the Scanner tab: cached history, fake feed, fixed Sunday clock."""

from __future__ import annotations

import csv

from textual.widgets import Input, OptionList, Static

from swingdash.ui.app import SwingDashApp
from swingdash.ui.tabs.rvol.pane import LiveRvolTab
from swingdash.ui.tabs.scanner.index_picker import IndexPicker
from swingdash.ui.tabs.scanner.pane import ScannerTab
from swingdash.ui.widgets.nav_table import NavTable
from tests.ui.conftest import INDEX_KEYS
from tests.ui.helpers import key, until

DEFAULT_LIST = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
# Steadier uptrend = higher Mswing (see DAILY_DRIFTS in conftest).
BY_MSWING = ["ICICIBANK", "HDFCBANK", "RELIANCE", "INFY", "TCS"]


def _tab(app: SwingDashApp) -> ScannerTab:
    return app.query_one(ScannerTab)


def _table(app: SwingDashApp) -> NavTable:
    return app.query_one("#scanner-table", NavTable)


def _symbols(app: SwingDashApp) -> list[str]:
    table = _table(app)
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


def _status(app: SwingDashApp) -> str:
    return str(_tab(app)._status_line.render())


async def _open(app: SwingDashApp, pilot) -> None:
    await pilot.press("3")
    await until(pilot, lambda: bool(app.query(ScannerTab)))
    await until(pilot, lambda: _symbols(app) == BY_MSWING)


async def test_opens_on_key_3_with_every_row_from_cached_history(services, feed):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)

        assert "NIFTY MIDSML 400 Mswing" in _status(app)
        assert "as of Fri 11 Sep close" in _status(app)
        subscribed = set(feed.transport.started_with or []) | {
            k for batch in feed.transport.subscribed for k in batch
        }
        assert INDEX_KEYS[0] in subscribed
        assert {key(s) for s in DEFAULT_LIST} <= subscribed

        top = [str(cell) for cell in _table(app).get_row_at(0)]
        assert top[0] == "ICICIBANK"
        assert top[3].strip().endswith("●")  # Burst Power with its dot
        assert top[8].startswith("+")  # a rising stock's Mswing


async def test_detail_panel_follows_the_cursor(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("down", "down")
        await pilot.pause()

        tab = _tab(app)
        row = tab._rows["RELIANCE"]
        assert tab._live.cursor_key() == "RELIANCE"
        assert row.metrics is not None and row.metrics.mswing.score is not None
        mswing = str(app.query_one("#scanner-mswing", Static).render())
        assert f"{row.metrics.mswing.score:+.2f}" in mswing
        assert "NIFTY MIDSML 400" in mswing

        await pilot.press("p")
        await pilot.pause()
        assert not app.query_one("#scanner-detail").display


async def test_sort_and_filter(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)

        await pilot.press("r")  # weakest Mswing first
        await until(pilot, lambda: _symbols(app) == list(reversed(BY_MSWING)))

        await pilot.press("s", "s")  # mswing -> vs IDX -> symbol (A-Z)
        await until(pilot, lambda: _symbols(app) == sorted(DEFAULT_LIST))

        await pilot.press("slash", *"bank", "enter")
        await until(pilot, lambda: _symbols(app) == ["HDFCBANK", "ICICIBANK"])
        await pilot.press("escape")
        await until(pilot, lambda: len(_symbols(app)) == 5)


async def test_picking_another_index_recomputes_and_is_remembered(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)

        await pilot.press("i")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, IndexPicker)
        picker.query_one("#index-filter", Input).value = "NIFTY 50"
        await pilot.pause()
        assert picker.query_one("#index-options", OptionList).option_count == 1
        await pilot.press("enter")

        await until(pilot, lambda: "NIFTY 50 Mswing" in _status(app))
        assert services.preferences.scanner_index() == INDEX_KEYS[1]


async def test_export_and_chart_use_the_rows_as_displayed(services, opened_urls):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)

        await pilot.press("x")
        await pilot.pause()
        exports = list(services.settings.paths.exports_dir.glob("scanner_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0][:4] == ["SYMBOL", "LTP", "CHG%", "BURST POWER"]
        assert [r[0] for r in rows[1:]] == BY_MSWING
        assert rows[1][16] == "NIFTY MIDSML 400"

        await pilot.press("o")
        await pilot.pause()
        assert opened_urls == ["https://in.tradingview.com/chart/?symbol=NSE%3AICICIBANK"]


async def test_typing_in_the_filter_never_triggers_shortcuts(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("slash", "i", "p", "x", "q", "1")
        await pilot.pause()
        assert app.query_one("#scanner-filter", Input).value == "ipxq1"
        assert not isinstance(app.screen, IndexPicker)
        assert app.is_running


async def test_live_rvol_keeps_working_alongside(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await until(pilot, lambda: app.query_one(LiveRvolTab)._table.row_count == 5)
        await _open(app, pilot)
        await pilot.press("1")
        await until(pilot, lambda: app.query_one(LiveRvolTab)._table.row_count == 5)
