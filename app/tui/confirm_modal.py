"""
Yes/no confirmation for destructive actions.

Deleting a watchlist can't be undone, so it gets a deliberate keystroke
rather than happening on a single stray keypress. Cancel is focused by
default, so Enter dismisses safely.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    ConfirmModal { align: center middle; }
    #dialog {
        width: 54; height: auto; padding: 1 2;
        background: $surface; border: thick $error;
    }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 1; }
    """

    def __init__(self, message: str, confirm_label: str = "Delete") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._message)
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, variant="error", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)
