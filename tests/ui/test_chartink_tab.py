"""Pilot tests for the Chartink tab: fake Chartink source, cached history, fixed Sunday clock."""

from __future__ import annotations

import csv

from textual.widgets import Input, Label, TextArea

from swingdash.domain.chartink import ChartinkResult, ChartinkRow
from swingdash.services.container import Services
from swingdash.ui.app import SwingDashApp
from swingdash.ui.tabs.chartink.add_modal import AddChartinkModal
from swingdash.ui.tabs.chartink.columns_modal import ColumnsPayloadModal
from swingdash.ui.tabs.chartink.dashboard_picker import DashboardPicker
from swingdash.ui.tabs.chartink.pane import ChartinkTab
from swingdash.ui.tabs.rvol.pane import LiveRvolTab
from swingdash.ui.watchlist.confirm_modal import ConfirmModal
from swingdash.ui.widgets.nav_table import NavTable
from swingdash.ui.widgets.prompt_modal import PromptModal
from tests.fakes.chartink import FIXTURES, FakeChartinkSource
from tests.ui.helpers import until

CLAUSE = "( {cash} ( my stocks ) )"


def _stocks(*symbols: str) -> ChartinkResult:
    return ChartinkResult(
        ("nsecode", "name", "close", "per_chg"),
        tuple(
            ChartinkRow(s, {"nsecode": s, "name": s.title(), "close": 100.0, "per_chg": 1.5})
            for s in symbols
        ),
        group_by="symbol",
    )


def _tab(app: SwingDashApp) -> ChartinkTab:
    return app.query_one(ChartinkTab)


def _table(app: SwingDashApp) -> NavTable:
    return app.query_one("#chartink-table", NavTable)


def _rows(app: SwingDashApp) -> list[list[str]]:
    table = _table(app)
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def _headers(app: SwingDashApp) -> list[str]:
    return [str(column.label) for column in _table(app).columns.values()]


def _header(app: SwingDashApp) -> str:
    return str(_tab(app)._header_line.render())


def _status(app: SwingDashApp) -> str:
    return str(_tab(app)._status_line.render())


async def _open(app: SwingDashApp, pilot) -> None:
    await pilot.press("4")
    await until(pilot, lambda: bool(app.query(ChartinkTab)))
    await pilot.pause()


async def _add(app: SwingDashApp, pilot, text: str, name: str = "") -> None:
    await pilot.press("a")
    await until(pilot, lambda: isinstance(app.screen, AddChartinkModal))
    app.screen.query_one("#paste", TextArea).text = text
    app.screen.query_one("#name", Input).value = name
    await pilot.click("#add")
    await until(pilot, lambda: not isinstance(app.screen, AddChartinkModal))


async def _add_stocks(app: SwingDashApp, pilot, source: FakeChartinkSource, *symbols: str) -> None:
    source.scripted["my stocks"] = _stocks(*symbols)
    await _open(app, pilot)
    await _add(app, pilot, CLAUSE, name="Mine")
    await until(pilot, lambda: _table(app).row_count == len(symbols))


async def test_opens_empty_on_key_4_without_asking_chartink(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await until(pilot, lambda: "No saved Chartink" in _header(app))
        assert _table(app).row_count == 0
        assert chartink_source.calls == []


async def test_pasted_clause_runs_and_gains_band_and_burst(
    services: Services, chartink_source, opened_urls
):
    services.securities.refresh_blocking(include_sectors=False)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND", "SBIN", "NOTLISTED")

        assert chartink_source.calls == [("screener", CLAUSE)]
        assert _headers(app) == ["SYMBOL", "NAME", "CLOSE", "% CHG", "BAND", "BURST"]
        await until(pilot, lambda: _rows(app)[0][5].strip().endswith("●"))
        raymond, _, unlisted = _rows(app)
        assert raymond[:4] == ["RAYMOND", "Raymond", "100.00", "+1.50"]
        assert raymond[4] == "20%"
        assert unlisted[4] == "-" and unlisted[5] == "-"
        assert "Mine" in _header(app) and "3 rows" in _header(app)
        assert "delayed ~5 min" in _header(app)

        _table(app).focus()
        await pilot.press("o")
        assert opened_urls == ["https://in.tradingview.com/chart/?symbol=NSE%3ARAYMOND"]
        await pilot.press("down", "down", "o")
        assert len(opened_urls) == 1  # NOTLISTED isn't an NSE symbol


async def test_bands_hint_until_securities_data_exists(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND")
        await until(pilot, lambda: "Securities tab" in _status(app))
        assert _rows(app)[0][4] == "-"


async def test_group_widgets_show_as_plain_tables(services, chartink_source, opened_urls):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, "select count(1) where {cash} ( 1 = 1 ) GROUP BY sector")
        await until(pilot, lambda: _table(app).row_count > 0)

        assert _headers(app)[0] == "SECTOR"
        assert "BAND" not in _headers(app)
        assert "not a stock list" in _status(app)
        assert "Widget 1" in _header(app)
        _table(app).focus()
        await pilot.press("o")
        assert opened_urls == []


async def test_typing_in_the_paste_box_fires_no_shortcuts(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("a")
        await until(pilot, lambda: isinstance(app.screen, AddChartinkModal))
        await pilot.press("R", "D", "W", "q", "4", "x", "a")

        assert isinstance(app.screen, AddChartinkModal)
        assert app.screen.query_one("#paste", TextArea).text == "RDWq4xa"
        await pilot.press("escape")
        await until(pilot, lambda: not isinstance(app.screen, AddChartinkModal))
        assert chartink_source.calls == []
        assert services.chartink.items() == []


async def test_bad_paste_explains_itself(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, "https://example.com/not-chartink")
        await pilot.pause()
        assert services.chartink.items() == []
        assert any("chartink.com" in str(n.message) for n in app._notifications)


async def test_screener_link_import_runs_it(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, "https://chartink.com/screener/consolidatedbo")
        await until(pilot, lambda: _table(app).row_count == 5)

        assert chartink_source.calls[0] == ("page", "https://chartink.com/screener/consolidatedbo")
        assert chartink_source.calls[1][0] == "screener"
        assert "consolidatedBO" in _header(app)
        assert _rows(app)[0][0] == "LT"


async def test_dashboard_import_picks_tables_and_runs_them_in_turn(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, "https://chartink.com/dashboard/130216")
        await until(pilot, lambda: isinstance(app.screen, DashboardPicker))
        await pilot.click("#import")
        await until(pilot, lambda: len(services.chartink.items()) == 2)
        await until(pilot, lambda: len(chartink_source.calls) == 3 and _table(app).row_count > 0)

        items = services.chartink.items()
        assert {item.collection for item in items} == {"Swing trade Dashboard"}
        assert [item.name for item in items] == [
            "Stocks near 52 week high",
            "Price EMA SMA 50 Days",
        ]
        assert [kind for kind, _ in chartink_source.calls] == ["page", "widget", "widget"]
        assert "Stocks near 52 week high" in _header(app)

        # A dashboard branch reruns every widget in it.
        _tab(app)._item_tree.focus()
        await pilot.press("up")  # from the first widget to its dashboard
        await until(pilot, lambda: "R runs them all" in _header(app))
        assert _table(app).row_count == 0
        await pilot.press("R")
        await until(pilot, lambda: len(chartink_source.calls) == 5)


async def test_rerun_failure_keeps_the_previous_result(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND", "SBIN")
        chartink_source.scripted["my stocks"] = RuntimeError("Chartink is rate limiting us")
        await pilot.press("R")
        await until(pilot, lambda: "last run failed" in _header(app))

        assert "rate limiting" in _header(app)
        assert _table(app).row_count == 2
        assert len(chartink_source.calls) == 2


async def test_save_as_watchlist_confirms_replacing_and_becomes_active(
    services: Services, chartink_source
):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND", "SBIN", "TCS", "NOTLISTED")
        await pilot.press("W")
        await until(pilot, lambda: isinstance(app.screen, PromptModal))
        assert app.screen.query_one(Input).value == "Mine"
        app.screen.query_one(Input).value = "nxtDay"  # already exists
        await pilot.click("#confirm")
        await until(pilot, lambda: isinstance(app.screen, ConfirmModal))
        await pilot.click("#confirm")
        await until(pilot, lambda: app.watchlist is not None and app.watchlist.name == "nxtDay")

        assert app.watchlist is not None
        assert app.watchlist.symbols == ("RAYMOND", "SBIN", "TCS")
        assert [w.name for w in services.watchlists.all()].count("nxtDay") == 1
        await pilot.press("1")
        await until(pilot, lambda: app.query_one(LiveRvolTab)._table.row_count == 3)


async def test_sort_filter_and_export(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "TCS", "RAYMOND", "SBIN")

        await pilot.press("s")  # symbol A-Z
        await until(pilot, lambda: [r[0] for r in _rows(app)] == ["RAYMOND", "SBIN", "TCS"])
        assert _headers(app)[0] == "SYMBOL ^"
        await pilot.press("r")
        await until(pilot, lambda: [r[0] for r in _rows(app)] == ["TCS", "SBIN", "RAYMOND"])

        await pilot.press("slash")
        await pilot.press("s", "b")
        await until(pilot, lambda: [r[0] for r in _rows(app)] == ["SBIN"])
        await pilot.press("enter")
        await pilot.press("x")
        await pilot.pause()

        exports = list(services.settings.paths.exports_dir.glob("chartink-mine_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["SYMBOL", "NAME", "CLOSE", "% CHG", "BAND", "BURST POWER"]
        assert [r[0] for r in rows[1:]] == ["SBIN"]


async def test_rename_and_delete(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND")

        await pilot.press("m")
        await until(pilot, lambda: isinstance(app.screen, PromptModal))
        app.screen.query_one(Input).value = "Breakouts"
        await pilot.click("#confirm")
        await until(pilot, lambda: "Breakouts" in _header(app))
        assert [item.name for item in services.chartink.items()] == ["Breakouts"]

        await pilot.press("D")
        await until(pilot, lambda: isinstance(app.screen, ConfirmModal))
        await pilot.click("#confirm")
        await until(pilot, lambda: "No saved Chartink" in _header(app))
        assert services.chartink.items() == []
        assert _table(app).row_count == 0


async def test_last_results_show_on_reopen_without_running(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _add_stocks(app, pilot, chartink_source, "RAYMOND", "SBIN")

    services.chartink._items = None  # as a fresh start would
    calls = len(chartink_source.calls)
    reopened = SwingDashApp(services, services.watchlists.get("default"))
    async with reopened.run_test(size=(160, 45)) as pilot:
        await _open(reopened, pilot)
        await until(pilot, lambda: _table(reopened).row_count == 2)
        assert len(chartink_source.calls) == calls


UNIVERSE = "https://chartink.com/screener/total-universe-v2"
PAYLOAD = (FIXTURES / "screener_payload_columns.json").read_text(encoding="utf-8")


def _cell_style(app: SwingDashApp, row: int, column: int) -> str:
    cell = _table(app).get_row_at(row)[column]
    return str(cell.style)


async def test_link_with_custom_columns_asks_for_the_payload(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(180, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, UNIVERSE)
        await until(pilot, lambda: isinstance(app.screen, ColumnsPayloadModal))
        assert "RVOL%, MSwing" in str(app.screen.query_one(Label).render())

        # Something that isn't a payload with columns is explained, not accepted.
        app.screen.query_one("#payload", TextArea).text = "( {cash} ( close > 15 ) )"
        await pilot.click("#add")
        await pilot.pause()
        assert isinstance(app.screen, ColumnsPayloadModal)
        assert "column_clause" in str(app.screen.query_one("#columns-error", Label).render())

        app.screen.query_one("#payload", TextArea).text = PAYLOAD
        await pilot.pause(0.3)  # a Button ignores a second click within 0.2s
        await pilot.click("#add")
        await until(pilot, lambda: _table(app).row_count == 3)

        assert _headers(app) == [
            "SYMBOL", "NAME", "CLOSE", "% CHG", "VOLUME", "RVOL%", "MSWING", "BAND", "BURST",
        ]  # fmt: skip
        divislab = _rows(app)[0]
        assert divislab[0] == "DIVISLAB" and divislab[5] == "117.98" and divislab[6] == "1.26"
        # Chartink's own colours: RVOL% < 120 red, MSwing > 0 green, % change red.
        assert _cell_style(app, 0, 5) == "#F23645"
        assert _cell_style(app, 0, 6) == "#4CAF50"
        assert _cell_style(app, 0, 3) == "#F23645"
        assert "Total Universe V2" in _header(app)
        assert "without its custom columns" not in _header(app)


async def test_link_and_payload_pasted_together_skip_the_question(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(180, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, f"{UNIVERSE}\n{PAYLOAD}")
        await until(pilot, lambda: _table(app).row_count == 3)
        assert "RVOL%" in _headers(app)
        assert not isinstance(app.screen, ColumnsPayloadModal)


async def test_skipping_the_payload_says_what_is_missing(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(180, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, UNIVERSE)
        await until(pilot, lambda: isinstance(app.screen, ColumnsPayloadModal))
        await pilot.click("#skip")
        await until(pilot, lambda: _table(app).row_count == 5)
        assert "without its custom columns (RVOL%, MSwing)" in _header(app)


async def test_payload_alone_shows_columns_and_how_to_name_them(services, chartink_source):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(180, 45)) as pilot:
        await _open(app, pilot)
        await _add(app, pilot, PAYLOAD, name="Universe")
        await until(pilot, lambda: _table(app).row_count == 3)
        headers = _headers(app)
        assert headers[:6] == ["SYMBOL", "NAME", "CLOSE", "% CHG", "VOLUME", "_7B5FD"]
        assert not any("CONDITIONAL" in h for h in headers)
        assert "column names: add the screener's link" in _status(app)
