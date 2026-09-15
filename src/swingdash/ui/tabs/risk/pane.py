"""
Risk tab: size a position before taking it, and track the risk of the
positions you hold.

Top, a sizing form (symbol, entry, stop by price / % / ATR / recent low,
risk as % of capital or ₹) with the result beside it: quantity, loss at the
stop with charges, what limited the size, R targets and warnings. Below, the
open positions with live P&L, open risk and portfolio heat.

All numbers come from RiskService (domain maths in domain/risk); this module
reads the form and renders. The form recalculates on every change and on
the refresh timer, so an auto-filled entry follows the live price.

The body is a ContentSwitcher with one view ("normal" delivery positions);
MTF sizing will be a second view.
"""

from __future__ import annotations

import time
from functools import partial
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.suggester import SuggestFromList
from textual.widgets import Button, ContentSwitcher, Input, Label, RadioButton, RadioSet, Static

from swingdash.domain.risk.checks import Severity
from swingdash.domain.risk.portfolio import Position
from swingdash.domain.risk.sizing import RiskMode, RiskSpec
from swingdash.domain.risk.stops import RiskInputError, StopMethod
from swingdash.services.dhan_sync import SyncStatus
from swingdash.services.risk import PortfolioView, PositionRow, SizedTrade, SymbolInfo
from swingdash.services.risk_settings import RiskSettings
from swingdash.ui.export import ExportTable
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.risk.format import grouped, inr, signed_inr
from swingdash.ui.tabs.risk.modals import (
    CloseForm,
    ClosePositionModal,
    DhanSyncModal,
    DhanSyncRequest,
    PositionForm,
    PositionModal,
    RemoveImportedModal,
    RiskSettingsModal,
)
from swingdash.ui.tabs.risk.positions import CLOSED_COLUMNS, OPEN_COLUMNS
from swingdash.ui.watchlist.confirm_modal import ConfirmModal
from swingdash.ui.widgets.nav_table import NavTable

# (method, radio label, label for the value input)
_STOP_METHODS: tuple[tuple[StopMethod, str, str], ...] = (
    (StopMethod.PRICE, "price", "Stop price"),
    (StopMethod.PERCENT, "% below", "% below entry"),
    (StopMethod.ATR, "ATR x", "ATR multiple"),
    (StopMethod.RECENT_LOW, "recent low", "Low of last days"),
)
_DEFAULT_STOP_PERCENT = "5"
_FORM_INPUTS = {"risk-symbol", "risk-entry", "risk-stop-value", "risk-value"}
_SEVERITY_STYLE = {
    Severity.DANGER: ("✖ ", "bold red"),
    Severity.WARN: ("⚠ ", "yellow"),
    Severity.INFO: ("• ", "grey62"),
}


class RiskTab(TabBase):
    REFRESH_HZ = 2.0

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+t", "take_trade", "Take trade"),
        Binding("S", "settings", "Settings"),
        Binding("m", "edit_position", "Edit"),
        Binding("C", "close_position", "Close"),
        Binding("D", "delete_position", "Delete"),
        Binding("h", "toggle_closed", "Open/closed"),
        Binding("B", "sync_broker", "Dhan sync"),
    ]

    def __init__(self) -> None:
        super().__init__(id="risk-view")
        # The entry value last filled in from the price (see _recalc).
        self._autofilled_entry: str | None = None
        self._sized: SizedTrade | None = None
        self._showing_closed = False
        self._table_signature: tuple[object, ...] | None = None
        self._open_rows: dict[str, PositionRow] = {}
        self._closed_rows: dict[str, Position] = {}
        self._portfolio: PortfolioView | None = None
        self._event_log: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(id="risk-summary")
        with ContentSwitcher(initial="risk-normal", id="risk-modes"), Vertical(id="risk-normal"):
            with Horizontal(id="risk-top"):
                with Vertical(id="risk-form"):
                    with Horizontal(classes="risk-row"):
                        yield Label("Symbol", classes="risk-label")
                        yield Input(placeholder="e.g. RAYMOND", id="risk-symbol")
                        yield Static(id="risk-quote", classes="risk-hint")
                    with Horizontal(classes="risk-row"):
                        yield Label("Entry", classes="risk-label")
                        yield Input(type="number", placeholder="price", id="risk-entry")
                    with Horizontal(classes="risk-row"):
                        yield Label("Stop by", classes="risk-label")
                        with RadioSet(id="risk-stop-method"):
                            for method, label, _ in _STOP_METHODS:
                                yield RadioButton(label, method is StopMethod.PERCENT)
                    with Horizontal(classes="risk-row"):
                        yield Label("% below entry", id="risk-stop-label", classes="risk-label")
                        yield Input(_DEFAULT_STOP_PERCENT, type="number", id="risk-stop-value")
                        yield Static(id="risk-stop-hint", classes="risk-hint")
                    with Horizontal(classes="risk-row"):
                        yield Label("Risk in", classes="risk-label")
                        with RadioSet(id="risk-mode"):
                            yield RadioButton("% of capital", True)
                            yield RadioButton("₹ amount")
                    with Horizontal(classes="risk-row"):
                        yield Label("Risk", classes="risk-label")
                        yield Input(type="number", id="risk-value")
                        yield Static(id="risk-value-hint", classes="risk-hint")
                    with Horizontal(classes="risk-row risk-buttons"):
                        yield Button("Take trade  ^t", variant="primary", id="risk-take")
                        yield Button("Settings  S", id="risk-settings")
                        yield Button("Dhan  B", id="risk-dhan")
                yield Static(id="risk-result")
            yield Static(id="risk-table-title")
            yield NavTable(id="risk-positions", cursor_type="row", zebra_stripes=True)
        yield Static(id="risk-status")

    # --- TabBase hooks -------------------------------------------------------

    def on_tab_mount(self) -> None:
        self._summary = self.query_one("#risk-summary", Static)
        self._symbol = self.query_one("#risk-symbol", Input)
        self._entry = self.query_one("#risk-entry", Input)
        self._stop_method = self.query_one("#risk-stop-method", RadioSet)
        self._stop_label = self.query_one("#risk-stop-label", Label)
        self._stop_value = self.query_one("#risk-stop-value", Input)
        self._risk_mode = self.query_one("#risk-mode", RadioSet)
        self._risk_value = self.query_one("#risk-value", Input)
        self._quote_hint = self.query_one("#risk-quote", Static)
        self._stop_hint = self.query_one("#risk-stop-hint", Static)
        self._risk_value_hint = self.query_one("#risk-value-hint", Static)
        self._result = self.query_one("#risk-result", Static)
        self._table_title = self.query_one("#risk-table-title", Static)
        self._table = self.query_one("#risk-positions", NavTable)
        self._status = self.query_one("#risk-status", Static)

        risk = self.services.risk
        self._symbol.suggester = SuggestFromList(risk.symbols(), case_sensitive=False)
        self._apply_risk_settings(risk.settings)
        self._symbol.focus()
        self._auto_sync_checked = float("-inf")

    def refresh_view(self) -> None:
        risk = self.services.risk
        self._event_log.extend(risk.events())
        self._event_log.extend(self.services.dhan.events())
        del self._event_log[:-3]
        self._maybe_auto_sync()
        view = risk.portfolio()
        self._portfolio = view
        self._summary.update(self._summary_text(view))
        self._draw_positions(view)
        self._recalc()
        self._status.update(self._status_text())

    def export_data(self) -> ExportTable | None:
        view = self._portfolio
        if view is None:
            return None
        capital = view.summary.capital
        if self._showing_closed:
            return ExportTable(
                name="risk-closed-positions",
                headers=[c.header for c in CLOSED_COLUMNS],
                rows=[[c.value(p, capital) for c in CLOSED_COLUMNS] for p in view.closed],
            )
        return ExportTable(
            name="risk-open-positions",
            headers=[c.header for c in OPEN_COLUMNS],
            rows=[[c.value(r, capital) for c in OPEN_COLUMNS] for r in view.open],
        )

    def chart_symbol(self) -> str | None:
        if not self._table.has_focus and self._sized is not None:
            return self._sized.info.symbol
        position = self._cursor_position()
        return position.symbol if position else None

    # --- form ---------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id not in _FORM_INPUTS:
            return
        if event.input.id == "risk-symbol":
            # A new symbol brings its own price, replacing whatever entry is there.
            self._autofilled_entry = self._entry.value
        self._safe_refresh()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        settings = self.services.risk.settings
        if event.radio_set.id == "risk-stop-method":
            method, _, label = _STOP_METHODS[event.index]
            self._stop_label.update(label)
            default = {
                StopMethod.PRICE: "",
                StopMethod.PERCENT: _DEFAULT_STOP_PERCENT,
                StopMethod.ATR: f"{settings.atr_multiple:g}",
                StopMethod.RECENT_LOW: str(settings.low_sessions),
            }[method]
            with self._stop_value.prevent(Input.Changed):
                self._stop_value.value = default
        elif event.radio_set.id == "risk-mode":
            with self._risk_value.prevent(Input.Changed):
                self._risk_value.value = (
                    f"{settings.risk_percent:g}"
                    if event.index == 0
                    else f"{settings.risk_amount:.0f}"
                )
        else:
            return
        self._safe_refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "risk-take":
            self.action_take_trade()
        elif event.button.id == "risk-settings":
            self.action_settings()
        elif event.button.id == "risk-dhan":
            self.action_sync_broker()

    def _recalc(self) -> None:
        risk = self.services.risk
        self._sized = None
        self._stop_hint.update("")
        spec = self._risk_spec()
        self._risk_value_hint.update(_risk_hint(spec, risk.settings.capital))

        symbol = self._symbol.value.strip().upper()
        risk.watch(symbol or None)
        if not symbol:
            self._quote_hint.update("")
            self._show_hint("Type a symbol to size a position.")
            return
        info = risk.info(symbol)
        if info is None:
            self._quote_hint.update(Text("not an NSE stock", style="red"))
            self._show_hint("Pick a symbol from the suggestions.")
            return
        self._quote_hint.update(_quote_text(info))
        # The entry follows the price while it's empty or still what we filled
        # in. Compared by value, not a flag set on Input.Changed: the refresh
        # timer can run between a keystroke and its Changed message.
        if info.quote is not None and self._entry.value in ("", self._autofilled_entry):
            price = f"{info.quote.price:.2f}"
            if self._entry.value != price:
                with self._entry.prevent(Input.Changed):
                    self._entry.value = price
            self._autofilled_entry = price

        entry = _number(self._entry.value)
        stop_value = _number(self._stop_value.value)
        if entry is None:
            self._show_hint("Enter an entry price.")
            return
        if stop_value is None:
            self._show_hint("Enter the stop.")
            return
        if spec is None:
            self._show_hint("Enter the risk per trade.")
            return
        method = _STOP_METHODS[max(0, self._stop_method.pressed_index)][0]
        try:
            sized = risk.size(symbol, entry, method, stop_value, spec)
        except RiskInputError as exc:
            self._result.update(Text(str(exc), style="red"))
            return
        self._sized = sized
        self._stop_hint.update(
            Text(f"→ {sized.stop:,.2f}  (-{sized.result.stop_pct:.1f}%)", style="cyan")
        )
        self._result.update(_result_text(sized, risk.settings.charges.name))

    def _risk_spec(self) -> RiskSpec | None:
        value = _number(self._risk_value.value)
        if value is None:
            return None
        mode = RiskMode.AMOUNT if self._risk_mode.pressed_index == 1 else RiskMode.PERCENT
        return RiskSpec(mode, value)

    def _show_hint(self, message: str) -> None:
        self._result.update(Text(message, style="grey62"))

    def _apply_risk_settings(self, settings: RiskSettings) -> None:
        percent = settings.risk_mode is RiskMode.PERCENT
        buttons = list(self._risk_mode.query(RadioButton))
        with self._risk_mode.prevent(RadioSet.Changed):
            buttons[0 if percent else 1].value = True
        with self._risk_value.prevent(Input.Changed):
            self._risk_value.value = (
                f"{settings.risk_percent:g}" if percent else f"{settings.risk_amount:.0f}"
            )

    # --- actions -------------------------------------------------------------

    def action_take_trade(self) -> None:
        self._recalc()
        sized = self._sized
        if sized is None or sized.result.quantity == 0:
            self.app.notify(
                "Size a trade first - it needs a quantity above zero.", severity="warning"
            )
            return
        result = sized.result
        self.app.push_screen(
            PositionModal(
                f"Take {sized.info.symbol}: check the fill before saving",
                result.quantity,
                result.entry,
                result.stop,
                self.services.calendar.today(),
                confirm_label="Add position",
            ),
            partial(self._trade_taken, sized.info.symbol),
        )

    def _trade_taken(self, symbol: str, form: PositionForm | None) -> None:
        if form is None:
            return
        try:
            self.services.risk.open_position(
                symbol, form.quantity, form.entry, form.stop, form.note, form.taken_on
            )
        except RiskInputError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self._showing_closed = False
        stop = "no stop-loss" if form.stop is None else f"stop {form.stop:,.2f}"
        self.app.notify(f"Added {symbol}: {grouped(form.quantity)} @ {form.entry:,.2f}, {stop}")
        self._safe_refresh()

    def action_settings(self) -> None:
        self.app.push_screen(RiskSettingsModal(self.services.risk.settings), self._settings_saved)

    def _settings_saved(self, settings: RiskSettings | None) -> None:
        if settings is None:
            return
        self.services.risk.save_settings(settings)
        self._apply_risk_settings(settings)
        self.app.notify("Risk settings saved.")
        self._safe_refresh()

    def action_edit_position(self) -> None:
        row = self._selected_open_row()
        if row is None:
            return
        position = row.position
        title = (
            f"{position.symbol} from {position.source.title()} - set or trail the stop"
            if position.is_imported
            else f"Edit {position.symbol} - trail the stop, or fix the fill"
        )
        self.app.push_screen(
            PositionModal(
                title,
                position.quantity,
                position.entry,
                position.stop,
                position.opened_on,
                position.note,
                locked=position.is_imported,
            ),
            partial(self._position_edited, position.id),
        )

    def _position_edited(self, position_id: int, form: PositionForm | None) -> None:
        if form is None:
            return
        try:
            self.services.risk.update_position(
                position_id,
                quantity=form.quantity,
                entry=form.entry,
                stop=form.stop,
                note=form.note,
                opened_on=form.taken_on,
            )
        except RiskInputError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self._safe_refresh()

    def action_close_position(self) -> None:
        row = self._selected_open_row()
        if row is None:
            return
        position = row.position
        if position.is_imported:
            self.app.notify(
                f"Exits come from {position.source.title()} - sell there, then sync (B).",
                severity="warning",
            )
            return
        self.app.push_screen(
            ClosePositionModal(
                f"Close {position.symbol} ({grouped(position.quantity)} @ {position.entry:,.2f})",
                row.quote.price if row.quote else None,
                self.services.calendar.today(),
            ),
            partial(self._position_closed, position.id, position.symbol),
        )

    def _position_closed(self, position_id: int, symbol: str, form: CloseForm | None) -> None:
        if form is None:
            return
        try:
            self.services.risk.close_position(position_id, form.exit_price, form.exited_on)
        except RiskInputError as exc:
            self.app.notify(str(exc), severity="error")
            return
        closed = next((p for p in self.services.risk.positions() if p.id == position_id), None)
        pnl = closed.realised_pnl if closed else None
        suffix = f": {signed_inr(pnl)} before charges" if pnl is not None else ""
        self.app.notify(f"Closed {symbol}{suffix}")
        self._safe_refresh()

    def action_delete_position(self) -> None:
        position = self._cursor_position()
        if position is None:
            self.app.notify("Select a position first.", severity="warning")
            return
        if position.is_imported:
            self.app.push_screen(
                RemoveImportedModal(position.symbol, position.source.title()),
                partial(self._imported_removed, position.id),
            )
            return
        self.app.push_screen(
            ConfirmModal(
                f"Delete {position.symbol}?\n"
                "This removes it entirely - to record an exit, close it (C)."
            ),
            partial(self._position_deleted, position.id),
        )

    def _position_deleted(self, position_id: int, confirmed: bool | None) -> None:
        if confirmed:
            self.services.risk.delete_position(position_id)
            self._safe_refresh()

    def _imported_removed(self, position_id: int, choice: str | None) -> None:
        if choice is None:
            return
        self.services.risk.delete_position(position_id, hide=choice == "hide")
        if choice == "hide":
            self.app.notify("Hidden - press B and tick 'Bring back' to restore it.")
        self._safe_refresh()

    def action_sync_broker(self) -> None:
        dhan = self.services.dhan
        if dhan.status.running:
            self.app.notify("A Dhan sync is already running.", severity="warning")
            return
        self._open_dhan_sync()

    def _open_dhan_sync(self) -> None:
        dhan = self.services.dhan
        status = dhan.status
        self.app.push_screen(
            DhanSyncModal(
                dhan.client_id(),
                _dhan_line(status).plain,
                needs_token=not status.connected or status.needs_token,
                history_from=dhan.history_from(),
                hidden=dhan.hidden_count(),
            ),
            self._dhan_sync_requested,
        )

    def _dhan_sync_requested(self, request: DhanSyncRequest | None) -> None:
        if request is None:
            return
        dhan = self.services.dhan
        try:
            if request.access_token:
                started = dhan.connect(
                    request.client_id,
                    request.access_token,
                    request.history_from,
                    restore_hidden=request.restore_hidden,
                )
            else:
                started = dhan.sync(request.history_from, restore_hidden=request.restore_hidden)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return
        if started:
            self.app.notify(f"Syncing Dhan trades since {request.history_from:%d %b %Y}...")
        self._safe_refresh()

    def _maybe_auto_sync(self) -> None:
        # The first look each day syncs by itself; checked at most once a minute.
        now = time.monotonic()
        if now - self._auto_sync_checked < 60:
            return
        self._auto_sync_checked = now
        dhan = self.services.dhan
        if dhan.status.connected and dhan.sync_if_due():
            self._event_log.append("syncing from Dhan...")

    def action_toggle_closed(self) -> None:
        self._showing_closed = not self._showing_closed
        self._safe_refresh()

    # --- positions table ------------------------------------------------------

    def _draw_positions(self, view: PortfolioView) -> None:
        capital = view.summary.capital
        self._open_rows = {str(r.position.id): r for r in view.open}
        self._closed_rows = {str(p.id): p for p in view.closed}
        if self._showing_closed:
            headers = [(c.key, c.header) for c in CLOSED_COLUMNS]
            cells = {
                key: tuple(c.cell(p, capital) for c in CLOSED_COLUMNS)
                for key, p in self._closed_rows.items()
            }
        else:
            headers = [(c.key, c.header) for c in OPEN_COLUMNS]
            cells = {
                key: tuple(c.cell(r, capital) for c in OPEN_COLUMNS)
                for key, r in self._open_rows.items()
            }
        self._table_title.update(self._table_title_text(view))

        signature = (self._showing_closed, tuple(cells.items()))
        if signature == self._table_signature:
            return
        mode_changed = (
            self._table_signature is None or self._table_signature[0] != self._showing_closed
        )
        self._table_signature = signature
        cursor_key = self._cursor_key()
        table = self._table
        table.clear(columns=mode_changed)
        if mode_changed:
            for key, header in headers:
                table.add_column(header, key=key)
        for key, row_cells in cells.items():
            table.add_row(*row_cells, key=key)
        if table.row_count:
            index = table.get_row_index(cursor_key) if cursor_key in cells else 0
            table.move_cursor(row=index)

    def _cursor_key(self) -> str | None:
        table = self._table
        if not table.row_count:
            return None
        try:
            return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _selected_open_row(self) -> PositionRow | None:
        if self._showing_closed:
            self.app.notify("Switch to open positions (h) to change one.", severity="warning")
            return None
        key = self._cursor_key()
        row = self._open_rows.get(key) if key else None
        if row is None:
            self.app.notify("Select an open position first.", severity="warning")
        return row

    def _cursor_position(self) -> Position | None:
        key = self._cursor_key()
        if key is None:
            return None
        if self._showing_closed:
            return self._closed_rows.get(key)
        row = self._open_rows.get(key)
        return row.position if row else None

    # --- text ----------------------------------------------------------------

    def _summary_text(self, view: PortfolioView) -> Text:
        summary = view.summary
        settings = self.services.risk.settings
        text = Text()
        if summary.capital <= 0:
            text.append("Set your capital and risk per trade first: ", style="yellow")
            text.append("press S", style="bold yellow")
            text.append(" (or the Settings button).", style="yellow")
            return text
        risk = settings.risk.amount(summary.capital)
        if settings.risk_mode is RiskMode.PERCENT:
            risk_text = f"{settings.risk_percent:g}% ({inr(risk)})"
        else:
            risk_text = f"{inr(risk)} ({risk / summary.capital * 100:.2f}%)"
        text.append(f"Capital {inr(summary.capital)}", style="bold")
        text.append(
            f"  ·  risk/trade {risk_text}  ·  max position {settings.max_allocation_pct:g}%"
        )
        text.append("  ·  heat ")
        ratio = summary.heat_pct / summary.heat_limit_pct if summary.heat_limit_pct else 1
        heat_style = "green" if ratio < 0.5 else "yellow" if ratio < 1 else "bold red"
        text.append(f"{summary.heat_pct:.1f}%", style=heat_style)
        if summary.assumed_heat > 0:
            # Part of the heat is measured to assumed stops (no SL, or SL already hit).
            text.append(f" ({summary.assumed_heat_pct:.1f}% assumed)", style="yellow")
        text.append(
            f" of {summary.heat_limit_pct:g}% ({inr(summary.heat)}, {inr(summary.heat_left)} left)"
        )
        text.append(f"  ·  free {inr(summary.free_capital)}  ·  {summary.open_count} open")
        if summary.open_count:
            change = summary.current_value - summary.capital_used
            text.append(
                f"  ·  invested {inr(summary.capital_used)} now {inr(summary.current_value)} "
            )
            text.append(signed_inr(change), style="green" if change >= 0 else "red")
        text.append(f"  ·  {settings.charges.name} charges", style="grey62")
        return text

    def _table_title_text(self, view: PortfolioView) -> Text:
        text = Text()
        if self._showing_closed:
            text.append(f"Closed positions ({len(view.closed)})", style="bold")
            realised = view.summary.realised_pnl
            if view.closed:
                text.append(
                    f"  realised {signed_inr(realised)} before charges",
                    style="green" if realised >= 0 else "red",
                )
                if view.summary.charges:
                    net = realised - view.summary.charges
                    text.append(f", {signed_inr(net)} after", style="green" if net >= 0 else "red")
                text.append("   DP EST: not in the broker's charges or net P&L", style="grey50")
            text.append("   h: open positions", style="grey50")
        else:
            text.append(f"Open positions ({len(view.open)})", style="bold")
            breaches = view.summary.breaches
            if breaches:
                counts = " · ".join(f"{n} {breach.value}" for breach, n in breaches.items())
                text.append(f"   Not followed: {counts}", style="bold red")
            elif view.open:
                text.append("   all following the plan", style="green")
            text.append("   m stop · C close · D delete · h closed", style="grey50")
        text.append("   ")
        text.append_text(_dhan_line(self.services.dhan.status))
        return text

    def _status_text(self) -> Text:
        text = Text(
            "LTP: live tick, else latest quote; dimmed = last daily close (no quote yet).  ",
            style="grey50",
        )
        mismatches = self.services.dhan.status.mismatches
        if mismatches:
            text.append(f"Dhan: {len(mismatches)} to check - {mismatches[0]}  ", style="yellow")
        if self._event_log:
            text.append(" | ".join(self._event_log[-2:]), style="yellow")
        return text


def _dhan_line(status: SyncStatus) -> Text:
    if not status.connected:
        return Text("Dhan: not connected (B)", style="grey50")
    if status.running:
        return Text(f"Dhan: {status.phase or 'syncing'}...", style="cyan")
    if status.needs_token:
        return Text("Dhan token expired - press B to paste a new one", style="bold red")
    if status.error:
        return Text(status.error, style="red")
    if status.last_sync is None:
        return Text("Dhan: connected, not synced yet (B)", style="yellow")
    return Text(f"Dhan synced {status.last_sync:%d %b %H:%M} (B)", style="grey62")


def _number(raw: str) -> float | None:
    try:
        return float(raw.strip().replace(",", ""))
    except ValueError:
        return None


def _risk_hint(spec: RiskSpec | None, capital: float) -> Text:
    """The per-trade risk in the other unit: '= ₹10,000' for 1%, '= 0.50%' for ₹5,000."""
    if spec is None or capital <= 0:
        return Text("")
    if spec.mode is RiskMode.PERCENT:
        return Text(f"= {inr(spec.amount(capital))}", style="grey62")
    return Text(f"= {spec.value / capital * 100:.2f}% of capital", style="grey62")


def _quote_text(info: SymbolInfo) -> Text:
    text = Text()
    if info.quote is None:
        text.append(
            "loading history..." if info.history_pending else "no price yet", style="yellow"
        )
        return text
    text.append(f"{info.quote.price:,.2f} ", style="bold")
    if info.quote.source == "live":
        text.append("live", style="green")
    elif info.quote.source == "quote":
        text.append(f"LTP at {info.quote.as_of or ''}", style="cyan")
    else:
        text.append(f"close {info.quote.as_of or ''} - no quote yet", style="yellow")
    if info.history_pending:
        text.append("  updating history...", style="yellow")
    return text


def _result_text(sized: SizedTrade, charges_name: str) -> Text:
    result, info = sized.result, sized.info
    text = Text()
    if result.quantity:
        text.append(f"Buy {grouped(result.quantity)} shares", style="bold green")
        if info.lot_size > 1:
            text.append(f" ({result.quantity // info.lot_size} lots of {info.lot_size})")
        text.append(
            f"  = {inr(result.position_value)}  ·  {result.allocation_pct:.1f}% of capital\n"
        )
    else:
        text.append("No position\n", style="bold red")
    text.append(
        f"Loss at stop {inr(result.loss_at_stop)} + {charges_name} charges "
        f"{inr(result.charges.total)} = "
    )
    text.append(inr(result.total_risk), style="bold")
    text.append(f"  ·  {result.risk_pct:.2f}% of capital\n")
    text.append(
        f"Stop {result.stop:,.2f} (-{result.stop_pct:.1f}%, {result.risk_per_share:,.2f}/share)"
        f"  ·  limited by {result.limited_by.value}"
    )
    others = [
        f"{limit.value} {grouped(quantity)}"
        for limit, quantity in result.allowed.items()
        if limit is not result.limited_by
    ]
    if others:
        text.append(f" ({', '.join(others)})", style="grey50")
    text.append("\nTargets ")
    for multiple in (1, 2, 3):
        text.append(f"  {multiple}R {result.target(multiple):,.2f}", style="cyan")
    text.append("\n")
    flags = [
        f"band {info.band.label}" if info.band else "band unknown (fetch Securities data)",
        ", ".join(info.surveillance.labels()) or "no surveillance",
        f"series {info.series}" if info.series else "",
        f"avg vol {grouped(info.avg_volume)}" if info.avg_volume else "",
    ]
    text.append("  ·  ".join(f for f in flags if f) + "\n", style="grey62")
    for warning in sized.warnings:
        icon, style = _SEVERITY_STYLE[warning.severity]
        text.append(f"{icon}{warning.message}\n", style=style)
    return text
