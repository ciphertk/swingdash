"""The Risk tab's dialogs: take / edit a position, close it, and the settings."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet

from swingdash.domain.risk.charges import Broker
from swingdash.domain.risk.sizing import RiskMode
from swingdash.services.risk_settings import RiskSettings
from swingdash.ui.tabs.risk.format import date_input, parse_date

_DIALOG_CSS = """
{name} {{ align: center middle; }}
{name} #dialog {{
    width: 64; height: auto; padding: 1 2;
    background: $surface; border: thick $accent;
}}
{name} #dialog > Label {{ color: $text-muted; }}
{name} Grid {{ grid-size: 2; grid-columns: 22 1fr; grid-rows: 3; height: auto; }}
{name} Grid Label {{ padding: 1 0 0 0; }}
{name} #form-error {{ color: $error; height: auto; }}
{name} #buttons {{ height: auto; align-horizontal: right; margin-top: 1; }}
{name} #buttons Button {{ margin-left: 1; }}
"""


class _FormModal[T](ModalScreen[T | None]):
    """Shared plumbing: Enter or the confirm button submits, Esc cancels, errors show inline."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        try:
            result = self.collect()
        except ValueError as exc:
            self.query_one("#form-error", Label).update(str(exc))
            return
        self.dismiss(result)

    def collect(self) -> T:
        raise NotImplementedError

    def _number(self, input_id: str, label: str, *, positive: bool = True) -> float:
        raw = self.query_one(f"#{input_id}", Input).value.strip().replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{label}: enter a number.") from None
        if positive and value <= 0:
            raise ValueError(f"{label} must be above zero.")
        return value

    def _optional_number(self, input_id: str, label: str) -> float | None:
        if not self.query_one(f"#{input_id}", Input).value.strip():
            return None
        return self._number(input_id, label)

    def _date(self, input_id: str) -> dt.date:
        return parse_date(self.query_one(f"#{input_id}", Input).value)

    def _integer(self, input_id: str, label: str) -> int:
        value = self._number(input_id, label)
        if value != int(value):
            raise ValueError(f"{label} must be a whole number.")
        return int(value)


@dataclass(frozen=True)
class PositionForm:
    quantity: int
    entry: float
    stop: float | None  # None: no stop-loss
    note: str
    taken_on: dt.date


@dataclass(frozen=True)
class CloseForm:
    exit_price: float
    exited_on: dt.date


class PositionModal(_FormModal[PositionForm]):
    """
    Take a sized trade, or edit an open position (e.g. trail its stop). A
    blank stop means no stop-loss. `locked`: an imported position, whose
    date, quantity and entry come from the broker - only stop and note edit.
    """

    CSS = _DIALOG_CSS.format(name="PositionModal")

    def __init__(
        self,
        title: str,
        quantity: int,
        entry: float,
        stop: float | None,
        taken_on: dt.date,
        note: str = "",
        confirm_label: str = "Save",
        *,
        locked: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._values = (quantity, entry, stop, taken_on, note)
        self._confirm_label = confirm_label
        self._locked = locked

    def compose(self) -> ComposeResult:
        quantity, entry, stop, taken_on, note = self._values
        with Vertical(id="dialog"):
            yield Label(self._title)
            with Grid():
                yield Label("Date taken")
                yield Input(
                    date_input(taken_on),
                    placeholder="DD-MM-YYYY",
                    id="taken-on",
                    disabled=self._locked,
                )
                yield Label("Quantity")
                yield Input(str(quantity), type="integer", id="quantity", disabled=self._locked)
                yield Label("Entry")
                yield Input(f"{entry:.2f}", type="number", id="entry", disabled=self._locked)
                yield Label("Stop (blank = none)")
                yield Input(
                    "" if stop is None else f"{stop:.2f}",
                    type="number",
                    placeholder="no stop-loss",
                    id="stop",
                )
                yield Label("Note")
                yield Input(note, id="note")
            yield Label("", id="form-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#stop" if self._locked else "#quantity", Input).focus()

    def collect(self) -> PositionForm:
        return PositionForm(
            quantity=self._integer("quantity", "Quantity"),
            entry=self._number("entry", "Entry"),
            stop=self._optional_number("stop", "Stop"),
            note=self.query_one("#note", Input).value.strip(),
            taken_on=self._date("taken-on"),
        )


class ClosePositionModal(_FormModal[CloseForm]):
    CSS = _DIALOG_CSS.format(name="ClosePositionModal")

    def __init__(self, title: str, price: float | None, exited_on: dt.date) -> None:
        super().__init__()
        self._title = title
        self._price = price
        self._exited_on = exited_on

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title)
            with Grid():
                yield Label("Exit price")
                yield Input(f"{self._price:.2f}" if self._price else "", type="number", id="exit")
                yield Label("Exit date")
                yield Input(date_input(self._exited_on), placeholder="DD-MM-YYYY", id="exited-on")
            yield Label("", id="form-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Close position", variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#exit", Input).focus()

    def collect(self) -> CloseForm:
        return CloseForm(self._number("exit", "Exit price"), self._date("exited-on"))


class RiskSettingsModal(_FormModal[RiskSettings]):
    CSS = (
        _DIALOG_CSS.format(name="RiskSettingsModal")
        + """
    RiskSettingsModal RadioSet { layout: horizontal; height: 3; border: none; }
    RiskSettingsModal #dialog { max-height: 95%; }
    RiskSettingsModal VerticalScroll { height: auto; max-height: 34; }
    """
    )

    def __init__(self, settings: RiskSettings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        s = self._settings
        with Vertical(id="dialog"):
            yield Label("Risk settings - used for every trade you size")
            with VerticalScroll(), Grid():
                yield Label("Capital (₹)")
                yield Input(f"{s.capital:.0f}" if s.capital else "", type="number", id="capital")
                yield Label("Broker (for charges)")
                with RadioSet(id="broker"):
                    for broker in Broker:
                        yield RadioButton(broker.value.title(), s.broker is broker)
                yield Label("Risk per trade in")
                with RadioSet(id="risk-mode"):
                    yield RadioButton("% of capital", s.risk_mode is RiskMode.PERCENT)
                    yield RadioButton("₹ amount", s.risk_mode is RiskMode.AMOUNT)
                yield Label("Risk % of capital")
                yield Input(f"{s.risk_percent:g}", type="number", id="risk-percent")
                yield Label("Risk amount (₹)")
                yield Input(f"{s.risk_amount:.0f}", type="number", id="risk-amount")
                yield Label("Max position % of capital")
                yield Input(f"{s.max_allocation_pct:g}", type="number", id="allocation")
                yield Label("Max portfolio heat %")
                yield Input(f"{s.max_heat_pct:g}", type="number", id="heat")
                yield Label("ATR period (days)")
                yield Input(str(s.atr_period), type="integer", id="atr-period")
                yield Label("ATR stop multiple")
                yield Input(f"{s.atr_multiple:g}", type="number", id="atr-multiple")
                yield Label("Recent low over (days)")
                yield Input(str(s.low_sessions), type="integer", id="low-sessions")
                yield Label("Liquidity warning %")
                yield Input(f"{s.liquidity_warn_pct:g}", type="number", id="liquidity")
                yield Label("No stop-loss: assume")
                with RadioSet(id="assumed-method"):
                    yield RadioButton("ATR multiple", s.assumed_stop_use_atr)
                    yield RadioButton("% below price", not s.assumed_stop_use_atr)
                yield Label("Assumed stop %")
                yield Input(f"{s.assumed_stop_pct:g}", type="number", id="assumed-pct")
                yield Label("Broker history (days)")
                yield Input(str(s.broker_history_days), type="integer", id="history-days")
            yield Label("", id="form-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#capital", Input).focus()

    def collect(self) -> RiskSettings:
        amount_mode = self.query_one("#risk-mode", RadioSet).pressed_index == 1
        broker = list(Broker)[max(0, self.query_one("#broker", RadioSet).pressed_index)]
        allocation = self._number("allocation", "Max position %")
        heat = self._number("heat", "Max heat %")
        assumed_pct = self._number("assumed-pct", "Assumed stop %")
        if allocation > 100 or heat > 100 or assumed_pct >= 100:
            raise ValueError("Percentages can't be above 100.")
        return RiskSettings(
            capital=self._number("capital", "Capital"),
            risk_mode=RiskMode.AMOUNT if amount_mode else RiskMode.PERCENT,
            risk_percent=self._number("risk-percent", "Risk %"),
            risk_amount=self._number("risk-amount", "Risk amount"),
            max_allocation_pct=allocation,
            max_heat_pct=heat,
            atr_period=self._integer("atr-period", "ATR period"),
            atr_multiple=self._number("atr-multiple", "ATR multiple"),
            low_sessions=self._integer("low-sessions", "Recent low days"),
            liquidity_warn_pct=self._number("liquidity", "Liquidity %"),
            broker=broker,
            assumed_stop_use_atr=self.query_one("#assumed-method", RadioSet).pressed_index != 1,
            assumed_stop_pct=assumed_pct,
            broker_history_days=self._integer("history-days", "Broker history days"),
        )


@dataclass(frozen=True)
class DhanSyncRequest:
    client_id: str
    access_token: str  # "" keeps the stored token
    history_from: dt.date
    restore_hidden: bool


class DhanSyncModal(_FormModal[DhanSyncRequest]):
    """
    Sync from Dhan: the account (client ID, and a token when one is needed -
    masked, never pre-filled), the date to build positions from, and whether
    to bring back hidden rows.
    """

    CSS = (
        _DIALOG_CSS.format(name="DhanSyncModal")
        + """
    DhanSyncModal #dialog { width: 80; }
    DhanSyncModal Checkbox { margin-top: 1; }
    """
    )

    def __init__(
        self,
        client_id: str,
        status: str,
        *,
        needs_token: bool,
        history_from: dt.date,
        hidden: int,
    ) -> None:
        super().__init__()
        self._client_id = client_id
        self._status = status
        self._needs_token = needs_token
        self._history_from = history_from
        self._hidden = hidden

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Sync from Dhan - read-only: swingdash never places orders")
            yield Label(self._status)
            if self._needs_token:
                yield Label(
                    "Get a token at web.dhan.co > My Profile > Access DhanHQ APIs > Access Token.\n"
                    "It lasts 24 hours; swingdash renews it while you keep using it.\n"
                    "It's stored in your user folder (like the Upstox token), never shown again."
                )
            with Grid():
                yield Label("Client ID")
                yield Input(self._client_id, placeholder="e.g. 1000000000", id="client-id")
                yield Label("Access token")
                yield Input(
                    password=True,
                    placeholder="paste it here"
                    if self._needs_token
                    else "blank keeps the current one",
                    id="access-token",
                )
                yield Label("Sync trades from")
                yield Input(
                    date_input(self._history_from), placeholder="DD-MM-YYYY", id="history-from"
                )
            if self._hidden:
                yield Checkbox(f"Bring back {self._hidden} hidden position(s)", id="restore-hidden")
            yield Label("", id="form-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Sync", variant="primary", id="confirm")

    def on_mount(self) -> None:
        if not self._client_id:
            target = "#client-id"
        elif self._needs_token:
            target = "#access-token"
        else:
            target = "#history-from"
        self.query_one(target, Input).focus()

    def collect(self) -> DhanSyncRequest:
        client_id = self.query_one("#client-id", Input).value.strip()
        token = self.query_one("#access-token", Input).value.strip()
        if not client_id:
            raise ValueError("Enter your Dhan client ID.")
        if not token and self._needs_token:
            raise ValueError("Paste the access token from web.dhan.co.")
        restore = self.query("#restore-hidden")
        return DhanSyncRequest(
            client_id=client_id,
            access_token=token,
            history_from=self._date("history-from"),
            restore_hidden=bool(restore) and self.query_one("#restore-hidden", Checkbox).value,
        )


class RemoveImportedModal(ModalScreen[str | None]):
    """Remove an imported row until the next sync, or hide it from syncs for good."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    RemoveImportedModal { align: center middle; }
    RemoveImportedModal #dialog {
        width: 70; height: auto; padding: 1 2;
        background: $surface; border: thick $error;
    }
    RemoveImportedModal #buttons { height: auto; align-horizontal: right; margin-top: 1; }
    RemoveImportedModal #buttons Button { margin-left: 1; }
    """

    def __init__(self, symbol: str, source: str) -> None:
        super().__init__()
        self._symbol = symbol
        self._source = source

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"Remove {self._symbol}? It stays in {self._source}.\n"
                "Remove: it comes back on the next sync.\n"
                "Hide: syncs skip it until you bring hidden positions back (B)."
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Remove", variant="warning", id="remove")
                yield Button("Hide for good", variant="error", id="hide")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id if event.button.id in ("remove", "hide") else None)

    def action_cancel(self) -> None:
        self.dismiss(None)
