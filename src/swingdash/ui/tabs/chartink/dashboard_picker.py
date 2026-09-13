"""Choose which of an imported dashboard's widgets to keep."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, SelectionList
from textual.widgets.selection_list import Selection

from swingdash.domain.chartink import DashboardDef, WidgetDef


class DashboardPicker(ModalScreen[list[WidgetDef] | None]):
    """Dismisses with the chosen widgets (possibly empty), or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    DashboardPicker { align: center middle; }
    DashboardPicker #dialog {
        width: 80; height: 30; padding: 1 2;
        background: $surface; border: thick $accent;
    }
    DashboardPicker #dialog Label { color: $text-muted; }
    DashboardPicker SelectionList { height: 1fr; margin: 1 0; }
    DashboardPicker #buttons { height: auto; align-horizontal: right; }
    DashboardPicker #buttons Button { margin-left: 1; }
    """

    def __init__(self, dashboard: DashboardDef) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        widgets = self._dashboard.widgets
        with Vertical(id="dialog"):
            yield Label(f"Import from '{self._dashboard.name}' ({len(widgets)} widgets)")
            yield Label(
                "Tables are stock lists and preselected; charts come in as a table of values. "
                "Space toggles."
            )
            yield SelectionList[int](
                *(
                    Selection(f"{w.name}  [{w.result_type or 'widget'}]", index, w.is_table)
                    for index, w in enumerate(widgets)
                ),
                id="widgets",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Import", variant="primary", id="import")

    def on_mount(self) -> None:
        self.query_one("#widgets", SelectionList).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "import":
            chosen = self.query_one("#widgets", SelectionList).selected
            self.dismiss([self._dashboard.widgets[i] for i in sorted(chosen)])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
