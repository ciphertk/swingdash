"""
Pilot tests for the dashboard shell against fake network seams: no socket,
no Upstox, a fixed Sunday clock.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Callable
from typing import ClassVar

from textual.widgets import Button, Input, TextArea

from swingdash.ui.app import SwingDashApp
from swingdash.ui.commands import DashboardCommands
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.registry import TABS, TabSpec
from swingdash.ui.tabs.rvol.pane import LiveRvolTab
from swingdash.ui.watchlist.edit_modal import WatchlistModal
from swingdash.ui.widgets.error_panel import ErrorPanel
from tests.ui.helpers import key as _key
from tests.ui.helpers import until as _until


def _rvol_tab(app: SwingDashApp) -> LiveRvolTab:
    return app.query_one(LiveRvolTab)


class CountingTab(TabBase):
    """A market-wide tab: no watchlist, just counts its refreshes."""

    REFRESH_HZ = 50.0
    instances: ClassVar[list[CountingTab]] = []

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        CountingTab.instances.append(self)

    def refresh_view(self) -> None:
        self.refreshes += 1


def _with_extra_tab(factory: Callable[[], TabBase]) -> tuple[TabSpec, ...]:
    return (TABS[0], TabSpec(id="breadth", title="Breadth", factory=factory))


async def test_opens_on_live_rvol_with_the_active_watchlist(services, feed):
    app = SwingDashApp(services, initial_watchlist=services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        assert feed.transport.started_with == sorted(
            _key(s) for s in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
        )
        feed.transport.tick(_key("TCS"), vtt=2_000, ltp=110.0, prev_close=100.0)
        await _until(
            pilot, lambda: "2.00x" in {str(c) for c in _rvol_tab(app)._table.get_row_at(0)}
        )
        assert "as of Fri 11 Sep close" in str(_rvol_tab(app)._status_line.render())


async def test_tabs_mount_lazily_and_hidden_tabs_stop_rendering(services):
    CountingTab.instances.clear()
    app = SwingDashApp(
        services, services.watchlists.get("default"), tabs=_with_extra_tab(CountingTab)
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert CountingTab.instances == []  # not built until opened

        await pilot.press("2")
        await _until(
            pilot, lambda: bool(CountingTab.instances) and CountingTab.instances[0].refreshes > 3
        )

        await pilot.press("1")
        await pilot.pause()
        paused_at = CountingTab.instances[0].refreshes
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert CountingTab.instances[0].refreshes == paused_at
        assert len(CountingTab.instances) == 1  # kept, not rebuilt


async def test_switching_the_global_watchlist_updates_the_tab_and_is_remembered(services, feed):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        app.select_watchlist("nxtDay")
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 2)
        assert services.watchlists.active_name() == "nxtDay"
        assert [_key("RAYMOND"), _key("SBIN")] in feed.transport.subscribed


async def test_a_broken_tab_does_not_take_down_the_dashboard(services):
    def explode() -> TabBase:
        raise RuntimeError("tab failed to build")

    app = SwingDashApp(services, services.watchlists.get("default"), tabs=_with_extra_tab(explode))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        await pilot.press("2")
        await _until(pilot, lambda: len(app.query(ErrorPanel)) == 1)
        await pilot.press("1")
        await pilot.pause()
        assert app.is_running
        assert _rvol_tab(app)._table.row_count == 5


async def test_a_tab_that_errors_while_rendering_is_contained(services):
    class Faulty(TabBase):
        REFRESH_HZ = 50.0

        def refresh_view(self) -> None:
            raise ValueError("bad data")

    app = SwingDashApp(services, services.watchlists.get("default"), tabs=_with_extra_tab(Faulty))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("2")
        await _until(pilot, lambda: len(app.query(".tab-error")) == 1)
        assert app.is_running


async def test_typing_in_the_filter_never_triggers_shortcuts(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        await pilot.press("slash", "t", "q", "n", "1")
        await pilot.pause()
        assert app.query_one("#rvol-filter", Input).value == "tqn1"
        assert app.is_running
        assert not isinstance(app.screen, WatchlistModal)


async def test_create_a_watchlist_from_the_header_shortcut(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        await pilot.press("n")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, WatchlistModal)
        modal.query_one("#name", Input).value = "banks"
        modal.query_one("#symbols", TextArea).text = "NSE:SBIN, icicibank\nHDFCBANK"
        modal.query_one("#save", Button).press()
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 3)
        assert services.watchlists.get("banks").symbols == ("SBIN", "ICICIBANK", "HDFCBANK")  # type: ignore[union-attr]
        assert app.watchlist is not None and app.watchlist.name == "banks"


async def test_export_writes_the_current_filtered_and_sorted_rows(services, feed):
    app = SwingDashApp(services, initial_watchlist=services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        feed.transport.tick(_key("TCS"), vtt=2_000, ltp=110.0, prev_close=100.0)
        await _until(
            pilot, lambda: "2.00x" in {str(c) for c in _rvol_tab(app)._table.get_row_at(0)}
        )
        # Narrow to one symbol - the export must reflect the filter, not the full watchlist.
        await pilot.press("slash", *"tcs", "enter")
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 1)

        await pilot.press("x")
        await pilot.pause()

        exports = list(services.settings.paths.exports_dir.glob("live-rvol_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["SYMBOL", "LTP", "CHG%", "VOLUME", "RVOL", "RVOL-D"]
        assert len(rows) == 2  # header + the one filtered row
        assert rows[1][0] == "TCS"
        assert rows[1][4] == "2.0"  # rvol, raw - not the "2.00x" shown on screen


async def test_pressing_o_opens_the_cursor_rows_chart(services, opened_urls):
    app = SwingDashApp(services, initial_watchlist=services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        _rvol_tab(app)._table.move_cursor(row=0)
        await pilot.pause()
        symbol = str(_rvol_tab(app)._table.get_row_at(0)[0]).rstrip("~")

        await pilot.press("o")
        await pilot.pause()

        assert opened_urls == [f"https://in.tradingview.com/chart/?symbol=NSE%3A{symbol}"]


async def test_enter_also_opens_the_cursor_rows_chart(services, opened_urls):
    app = SwingDashApp(services, initial_watchlist=services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await _until(pilot, lambda: _rvol_tab(app)._table.row_count == 5)
        _rvol_tab(app)._table.move_cursor(row=1)
        await pilot.pause()
        symbol = str(_rvol_tab(app)._table.get_row_at(1)[0]).rstrip("~")

        await pilot.press("enter")
        await pilot.pause()

        assert opened_urls == [f"https://in.tradingview.com/chart/?symbol=NSE%3A{symbol}"]


async def test_o_notifies_instead_of_opening_a_browser_when_theres_nothing_to_chart(
    services, opened_urls
):
    app = SwingDashApp(services, initial_watchlist=None)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert opened_urls == []


async def test_export_notifies_when_there_is_nothing_to_export(services):
    app = SwingDashApp(services, initial_watchlist=None)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert not list(services.settings.paths.exports_dir.glob("*.csv"))


async def test_renaming_a_watchlist_in_the_edit_dialog_does_not_leave_a_copy(services):
    app = SwingDashApp(services, services.watchlists.get("nxtDay"))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, WatchlistModal)
        modal.query_one("#name", Input).value = "00nxtDay"
        modal.query_one("#save", Button).press()
        await _until(pilot, lambda: app.watchlist is not None and app.watchlist.name == "00nxtDay")

        names = [w.name for w in services.watchlists.all()]
        assert "00nxtDay" in names and "nxtDay" not in names
        assert services.watchlists.active_name() == "00nxtDay"


async def test_the_edit_dialog_refuses_a_name_another_list_already_has(services):
    app = SwingDashApp(services, services.watchlists.get("nxtDay"))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#name", Input).value = "default"
        modal.query_one("#save", Button).press()
        await pilot.pause()

        assert app.screen is modal  # stays open so you can pick another name
        assert services.watchlists.get("nxtDay") is not None
        assert services.watchlists.get("default").symbols != ("RAYMOND", "SBIN")  # type: ignore[union-attr]


async def test_command_palette_offers_watchlists_and_tabs(services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        provider = DashboardCommands(app.screen)
        found = [str(hit.text) async for hit in provider.search("nxt")]
        assert any("nxtDay" in text for text in found)
        tabs = [str(hit.text) async for hit in provider.search("Live RVOL")]
        assert any("Live RVOL" in text for text in tabs)
