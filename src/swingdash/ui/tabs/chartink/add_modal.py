"""Add a Chartink screener or widget by pasting a link, a payload or a clause."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

_HELP = (
    "Paste any of:\n"
    "  a link   https://chartink.com/screener/...  or  /dashboard/...\n"
    "  a request payload from the browser's network tab (screener/process or widget/process)\n"
    "  a screener link, then its payload on the next line - names its custom columns\n"
    "  a scan clause  ( {cash} ( ... ) )   or a widget query  select ..."
)


class AddChartinkModal(ModalScreen[tuple[str, str] | None]):
    """Dismisses with (name, pasted text), or None if cancelled. The name may be empty."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    AddChartinkModal { align: center middle; }
    AddChartinkModal #dialog {
        width: 92; height: auto; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    AddChartinkModal #dialog Label { color: $text-muted; }
    AddChartinkModal #paste { height: 12; margin-bottom: 1; }
    AddChartinkModal #name { margin-bottom: 1; }
    AddChartinkModal #buttons { height: auto; align-horizontal: right; }
    AddChartinkModal #buttons Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add from Chartink")
            yield Label(_HELP)
            yield TextArea(id="paste")
            yield Label("Name (optional - a link brings its own)")
            yield Input(placeholder="e.g. Smart money", id="name")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="primary", id="add")

    def on_mount(self) -> None:
        self.query_one("#paste", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            self._add()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._add()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _add(self) -> None:
        text = self.query_one("#paste", TextArea).text
        if not text.strip():
            self.query_one("#paste", TextArea).focus()
            return
        self.dismiss((self.query_one("#name", Input).value.strip(), text))
