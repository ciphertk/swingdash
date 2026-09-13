"""
Asked when a screener imported by link has custom columns: Chartink builds
those in the browser, so only the screener's request payload can bring them.
"""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, TextArea

from swingdash.domain.chartink import (
    ChartinkInputError,
    ChartinkKind,
    ChartinkRequest,
    ScreenerDef,
    parse_user_input,
)

Answer = ChartinkRequest | Literal["skip"] | None


class ColumnsPayloadModal(ModalScreen[Answer]):
    """Dismisses with the pasted payload, "skip" (add without the columns), or None (cancel)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    ColumnsPayloadModal { align: center middle; }
    ColumnsPayloadModal #dialog {
        width: 92; height: auto; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    ColumnsPayloadModal #dialog Label { color: $text-muted; }
    ColumnsPayloadModal #columns-error { color: $error; }
    ColumnsPayloadModal #payload { height: 10; margin: 1 0; }
    ColumnsPayloadModal #buttons { height: auto; align-horizontal: right; }
    ColumnsPayloadModal #buttons Button { margin-left: 1; }
    """

    def __init__(self, screener: ScreenerDef) -> None:
        super().__init__()
        self._screener = screener

    def compose(self) -> ComposeResult:
        names = ", ".join(self._screener.custom_columns)
        with Vertical(id="dialog"):
            yield Label(f"'{self._screener.name}' has custom columns: {names}")
            yield Label(
                "Chartink builds those in the browser, so a link alone can't fetch them.\n"
                "To include them: open the screener on chartink.com, press F12 > Network,\n"
                "click Run Scan, select the 'process' request > Payload, copy it and paste here."
            )
            yield TextArea(id="payload")
            yield Label("", id="columns-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add without them", id="skip")
                yield Button("Add with columns", variant="primary", id="add")

    def on_mount(self) -> None:
        self.query_one("#payload", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            self._add()
        elif event.button.id == "skip":
            self.dismiss("skip")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _add(self) -> None:
        error = self.query_one("#columns-error", Label)
        try:
            request = parse_user_input(self.query_one("#payload", TextArea).text)
        except ChartinkInputError as exc:
            error.update(str(exc))
            return
        if not isinstance(request, ChartinkRequest) or request.kind is not ChartinkKind.SCREENER:
            error.update("That isn't a screener payload (it needs a scan_clause).")
        elif "column_clause" not in request.fields:
            error.update("That payload has no column_clause - copy the whole payload.")
        else:
            self.dismiss(request)
