"""Pilot tests for the Risk tab: real services, fake feed, cached history, fixed Sunday clock."""

from __future__ import annotations

import csv
from dataclasses import replace

import pytest
from textual.widgets import Input, RadioButton, RadioSet

from swingdash.domain.risk.sizing import RiskMode, RiskSpec
from swingdash.domain.risk.stops import StopMethod
from swingdash.services.container import Services
from swingdash.ui.app import SwingDashApp
from swingdash.ui.tabs.risk.modals import ClosePositionModal, PositionModal, RiskSettingsModal
from swingdash.ui.tabs.risk.pane import RiskTab
from swingdash.ui.watchlist.confirm_modal import ConfirmModal
from swingdash.ui.widgets.nav_table import NavTable
from tests.ui.helpers import until

CAPITAL = 1_000_000


def _tab(app: SwingDashApp) -> RiskTab:
    return app.query_one(RiskTab)


def _text(app: SwingDashApp, widget_id: str) -> str:
    return str(app.query_one(f"#{widget_id}").render())


def _table(app: SwingDashApp) -> NavTable:
    return app.query_one("#risk-positions", NavTable)


def _rows(app: SwingDashApp) -> list[list[str]]:
    table = _table(app)
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def _with_capital(services: Services) -> None:
    services.risk.save_settings(replace(services.risk.settings, capital=CAPITAL))


async def _open(app: SwingDashApp, pilot) -> None:
    await pilot.press("5")
    await until(pilot, lambda: bool(app.query(RiskTab)))
    await pilot.pause()


async def _size(app: SwingDashApp, pilot, symbol: str = "RAYMOND") -> None:
    app.query_one("#risk-symbol", Input).value = symbol
    await until(pilot, lambda: "Buy " in _text(app, "risk-result"))


def _choose(app: SwingDashApp, radio_set_id: str, index: int) -> None:
    list(app.screen.query_one(f"#{radio_set_id}", RadioSet).query(RadioButton))[index].value = True


async def test_asks_for_capital_first_and_settings_persist(services: Services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        assert "Set your capital" in _text(app, "risk-summary")
        app.query_one("#risk-symbol", Input).value = "RAYMOND"
        await until(pilot, lambda: "Set your capital first" in _text(app, "risk-result"))

        await pilot.click("#risk-settings")
        await until(pilot, lambda: isinstance(app.screen, RiskSettingsModal))
        app.screen.query_one("#capital", Input).value = "1000000"
        app.screen.query_one("#heat", Input).value = "8"
        await pilot.click("#confirm")
        await until(pilot, lambda: "Capital ₹10,00,000" in _text(app, "risk-summary"))

        assert services.risk.settings.capital == CAPITAL
        assert services.risk.settings.max_heat_pct == 8
        await until(pilot, lambda: "Buy " in _text(app, "risk-result"))


async def test_settings_reject_bad_numbers(services: Services):
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        await pilot.click("#risk-settings")
        await until(pilot, lambda: isinstance(app.screen, RiskSettingsModal))
        app.screen.query_one("#capital", Input).value = ""
        await pilot.click("#confirm")
        await pilot.pause()
        assert isinstance(app.screen, RiskSettingsModal)
        assert "Capital" in str(app.screen.query_one("#form-error").render())


async def test_sizing_follows_the_form(services: Services):
    services.securities.refresh_blocking(include_sectors=False)
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        await _size(app, pilot)

        # Entry auto-fills with the last close; the result matches the service.
        entry = float(app.query_one("#risk-entry", Input).value)
        expected = services.risk.size("RAYMOND", entry, StopMethod.PERCENT, 5)
        result = _text(app, "risk-result")
        assert f"Buy {expected.result.quantity:,} shares" in result
        assert "close 2026-09-11" in _text(app, "risk-quote")
        assert "band 20%" in result and "STASM-I" in result
        assert "= ₹10,000" in _text(app, "risk-value-hint")

        # ATR stop: the value box takes the ATR multiple from settings.
        _choose(app, "risk-stop-method", 2)
        await pilot.pause()
        assert app.query_one("#risk-stop-value", Input).value == "1.5"
        atr = services.risk.size("RAYMOND", entry, StopMethod.ATR, 1.5)
        await until(pilot, lambda: f"{atr.stop:,.2f}" in _text(app, "risk-stop-hint"))

        # Fixed ₹ risk.
        _choose(app, "risk-mode", 1)
        await pilot.pause()
        assert app.query_one("#risk-value", Input).value == "5000"
        await until(pilot, lambda: "0.50% of capital" in _text(app, "risk-value-hint"))
        fixed = services.risk.size(
            "RAYMOND", entry, StopMethod.ATR, 1.5, RiskSpec(RiskMode.AMOUNT, 5_000)
        )
        await until(
            pilot, lambda: f"Buy {fixed.result.quantity:,} shares" in _text(app, "risk-result")
        )

        # Typing an entry stops it following the price.
        app.query_one("#risk-entry", Input).value = "300"
        await pilot.pause(0.6)
        assert app.query_one("#risk-entry", Input).value == "300"


async def test_unknown_symbol_says_so(services: Services):
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        app.query_one("#risk-symbol", Input).value = "NOPE"
        await until(pilot, lambda: "not an NSE stock" in _text(app, "risk-quote"))


async def test_take_edit_close_and_delete_positions(services: Services):
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        await _size(app, pilot)
        quantity = _tab(app)._sized.result.quantity  # type: ignore[union-attr]

        await pilot.press("ctrl+t")
        await until(pilot, lambda: isinstance(app.screen, PositionModal))
        assert app.screen.query_one("#quantity", Input).value == str(quantity)
        assert app.screen.query_one("#taken-on", Input).value == "13-09-2026"  # today
        app.screen.query_one("#taken-on", Input).value = "10-09-2026"
        await pilot.click("#confirm")
        await until(pilot, lambda: _table(app).row_count == 1)
        assert _rows(app)[0][0] == "RAYMOND"
        assert _rows(app)[0][-2:] == ["10 Sep 26", "3"]  # taken, days held
        assert "1 open" in _text(app, "risk-summary")
        assert services.risk.portfolio().summary.heat > 0

        # Trail the stop above entry: no open risk left.
        _table(app).focus()
        await pilot.press("m")
        await until(pilot, lambda: isinstance(app.screen, PositionModal))
        entry = float(app.screen.query_one("#entry", Input).value)
        app.screen.query_one("#stop", Input).value = f"{entry + 1:.2f}"
        await pilot.click("#confirm")
        await until(pilot, lambda: services.risk.portfolio().summary.heat == 0)
        await until(pilot, lambda: _rows(app)[0][8] == "₹0")

        # Close at a price: it moves to the closed list with its P&L.
        await pilot.press("C")
        await until(pilot, lambda: isinstance(app.screen, ClosePositionModal))
        app.screen.query_one("#exit", Input).value = f"{entry + 10:.2f}"
        app.screen.query_one("#exited-on", Input).value = "12-09-2026"
        await pilot.click("#confirm")
        await until(pilot, lambda: _table(app).row_count == 0)
        assert services.risk.portfolio().summary.realised_pnl == pytest.approx(quantity * 10)

        await pilot.press("h")
        await until(pilot, lambda: _table(app).row_count == 1)
        assert "Closed positions (1)" in _text(app, "risk-table-title")
        closed_row = _rows(app)[0]
        assert closed_row[-3:] == ["10 Sep 26", "12 Sep 26", "2"]  # taken, exited, days

        await pilot.press("D")
        await until(pilot, lambda: isinstance(app.screen, ConfirmModal))
        await pilot.click("#confirm")
        await until(pilot, lambda: services.risk.positions() == [])


async def test_typing_in_the_form_fires_no_shortcuts(services: Services):
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        app.query_one("#risk-symbol", Input).focus()
        await pilot.press("S", "m", "C", "D", "h", "x", "o", "q", "5")
        assert app.query_one("#risk-symbol", Input).value == "SmCDhxoq5"
        assert not isinstance(app.screen, RiskSettingsModal)
        assert app.return_code is None  # q didn't quit


async def test_export_and_chart(services: Services, opened_urls):
    _with_capital(services)
    services.risk.open_position("SBIN", 100, 60, 55)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        await until(pilot, lambda: _table(app).row_count == 1)
        _table(app).focus()
        await pilot.press("o")
        assert opened_urls == ["https://in.tradingview.com/chart/?symbol=NSE%3ASBIN"]

        await pilot.press("x")
        await pilot.pause()
        exports = list(services.settings.paths.exports_dir.glob("risk-open-positions_*.csv"))
        assert len(exports) == 1
        with exports[0].open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[0][:5] == ["SYMBOL", "QTY", "ENTRY", "STOP", "LTP"]
        assert rows[1][:4] == ["SBIN", "100", "60.0", "55.0"]


async def test_a_bad_date_is_explained_in_the_dialog(services: Services):
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        await _size(app, pilot)
        await pilot.press("ctrl+t")
        await until(pilot, lambda: isinstance(app.screen, PositionModal))
        app.screen.query_one("#taken-on", Input).value = "last tuesday"
        await pilot.click("#confirm")
        await pilot.pause()
        assert isinstance(app.screen, PositionModal)
        assert "DD-MM-YYYY" in str(app.screen.query_one("#form-error").render())


async def test_broker_choice_changes_the_charges(services: Services):
    _with_capital(services)
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 50)) as pilot:
        await _open(app, pilot)
        await _size(app, pilot)
        assert "Upstox charges" in _text(app, "risk-result")

        await pilot.click("#risk-settings")
        await until(pilot, lambda: isinstance(app.screen, RiskSettingsModal))
        _choose(app, "broker", 1)  # Dhan
        await pilot.pause()
        await pilot.click("#confirm")
        await until(pilot, lambda: "Dhan charges" in _text(app, "risk-result"))
        assert "Dhan charges" in _text(app, "risk-summary")
        assert services.risk.settings.broker.value == "dhan"


async def test_quote_price_is_labelled(services: Services, quotes):
    _with_capital(services)
    quotes.prices = {"NSE_EQ|RAYMOND": 301.25}
    app = SwingDashApp(services, services.watchlists.get("default"))
    async with app.run_test(size=(150, 45)) as pilot:
        await _open(app, pilot)
        app.query_one("#risk-symbol", Input).value = "RAYMOND"
        await until(pilot, lambda: "301.25 LTP at" in _text(app, "risk-quote"))
        await until(pilot, lambda: app.query_one("#risk-entry", Input).value == "301.25")
