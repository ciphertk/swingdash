"""
DataTable with navigation suited to a long scanner list.

Stock DataTable binds home/end to horizontal scrolling, which does
nothing useful on a table that scrolls vertically - the cursor stays put
and the view snaps back. Here they jump the cursor to the first/last row,
and vim-style g/G do the same.
"""
from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable


class RvolTable(DataTable):
    BINDINGS = [
        Binding("home", "cursor_first", "Top", show=False),
        Binding("end", "cursor_last", "Bottom", show=False),
        Binding("g", "cursor_first", "Top", show=False),
        Binding("G", "cursor_last", "Bottom", show=False),
    ]

    def action_cursor_first(self) -> None:
        if self.row_count:
            self.move_cursor(row=0, scroll=True)

    def action_cursor_last(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1, scroll=True)
