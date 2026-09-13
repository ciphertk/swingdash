"""Pick the benchmark index Mswing compares every stock against."""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option


class IndexPicker(ModalScreen[str | None]):
    """Dismisses with the chosen index's instrument key, or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    IndexPicker { align: center middle; }
    #dialog {
        width: 56; height: 24; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    #dialog Label { color: $text-muted; }
    #dialog OptionList { height: 1fr; }
    """

    def __init__(self, indices: Sequence[tuple[str, str]], current: str) -> None:
        """`indices`: (instrument_key, display name) pairs."""
        super().__init__()
        self._indices = sorted(indices, key=lambda pair: pair[1])
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Mswing benchmark index (type to filter, Enter to pick)")
            yield Input(placeholder="filter indices...", id="index-filter")
            yield OptionList(id="index-options")

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#index-filter", Input).focus()

    def _fill(self, needle: str) -> None:
        options = self.query_one("#index-options", OptionList)
        options.clear_options()
        needle = needle.strip().upper()
        matches = [(key, name) for key, name in self._indices if needle in name.upper()]
        options.add_options(Option(name, id=key) for key, name in matches)
        keys = [key for key, _ in matches]
        if keys:
            options.highlighted = keys.index(self._current) if self._current in keys else 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "index-filter":
            event.stop()
            self._fill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "index-filter":
            return
        event.stop()
        options = self.query_one("#index-options", OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
