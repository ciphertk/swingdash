"""
Modal for creating or editing a watchlist by pasting symbols.

Accepts whatever shape the paste arrives in - comma or newline separated,
mixed case, duplicated - because watchlists usually come pasted out of a
screener or a chat message. watchlist_service.parse_symbols_text does the
normalising.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea


class WatchlistModal(ModalScreen[tuple[str, str] | None]):
    """Dismisses with (name, raw_symbols_text), or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    WatchlistModal { align: center middle; }
    #dialog {
        width: 64; height: auto; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    #dialog Label { color: $text-muted; }
    #dialog TextArea { height: 12; margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, name: str = "", symbols_text: str = "") -> None:
        super().__init__()
        self._title = title
        self._name = name
        self._symbols_text = symbols_text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title)
            yield Input(value=self._name, placeholder="watchlist name", id="name")
            yield Label("Symbols (comma or newline separated)")
            yield TextArea(self._symbols_text, id="symbols")
            with Horizontal(id="buttons"):
                yield Button("Clear", id="clear")
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="save")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "clear":
            # Empties the box so you can paste a fresh list; nothing is
            # saved until Save, so this is reversible by cancelling.
            area = self.query_one("#symbols", TextArea)
            area.text = ""
            area.focus()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the name field jumps to the paste area rather than
        # saving an empty watchlist.
        self.query_one("#symbols", TextArea).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        text = self.query_one("#symbols", TextArea).text
        if not name or not text.strip():
            self.query_one("#name", Input).focus()
            return  # nothing useful to save; keep the dialog open
        self.dismiss((name, text))
