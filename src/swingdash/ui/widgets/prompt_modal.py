"""A one-line text prompt (rename, name a new watchlist, ...)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class PromptModal(ModalScreen[str | None]):
    """Dismisses with the entered text (stripped, non-empty), or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    PromptModal { align: center middle; }
    PromptModal #dialog {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    PromptModal #dialog Label { color: $text-muted; }
    PromptModal #buttons { height: auto; align-horizontal: right; margin-top: 1; }
    PromptModal #buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, value: str = "", confirm_label: str = "OK") -> None:
        super().__init__()
        self._title = title
        self._value = value
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title)
            yield Input(value=self._value, id="prompt-input")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self) -> None:
        value = self.query_one("#prompt-input", Input).value.strip()
        if value:
            self.dismiss(value)
