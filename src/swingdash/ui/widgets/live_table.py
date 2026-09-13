"""
LiveTable - keeps a NavTable in step with rows whose values change every
frame, cheaply enough for hundreds of rows.

DataTable's costs drive the shape (measured on the Securities tab): adding
rows re-measures every cell (~0.5s for ~2,300 rows) while updating a cell
or reordering with DataTable.sort is ~30ms. So:

- the table is rebuilt only when the SET of rows changes (filter, watchlist);
- values are written cell by cell, and only where they changed;
- order is re-sorted in place, and at most every REORDER_SECONDS - never
  while you're navigating, or while frozen - so rows don't slide out from
  under the cursor mid-glance.

Row keys must equal the plain text of the first column, which is what
DataTable.sort is given to rank rows by.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from rich.text import Text
from textual.coordinate import Coordinate

from swingdash.ui.widgets.nav_table import NavTable

REORDER_SECONDS = 2.0
NAV_FREEZE_SECONDS = 5.0


class LiveTable:
    def __init__(self, table: NavTable, column_keys: Sequence[str]) -> None:
        self.table = table
        self._columns = list(column_keys)
        self._rendered: dict[str, tuple[Text, ...]] = {}
        self._order: list[str] = []
        self._last_reorder = 0.0
        self._nav_until = 0.0
        self.frozen = False
        # Until you move the cursor it stays on the top row, so the best row
        # is under it; after you navigate, it follows that row instead.
        self._follow_cursor = False

    @property
    def order(self) -> list[str]:
        """Row keys in the order currently displayed."""
        return list(self._order)

    @property
    def holding(self) -> bool:
        return time.monotonic() < self._nav_until

    def navigated(self) -> None:
        self._nav_until = time.monotonic() + NAV_FREEZE_SECONDS
        self._follow_cursor = True

    def reset(self) -> None:
        """Forget what's drawn (e.g. a watchlist switch) so the next render rebuilds."""
        self._rendered.clear()
        self._order = []
        self._follow_cursor = False

    def render(
        self,
        cells: Mapping[str, tuple[Text, ...]],
        order: Sequence[str],
        *,
        reorder_now: bool = False,
    ) -> None:
        """`cells` for every visible row; `order` is how they'd ideally be sorted now."""
        if set(cells) != set(self._rendered):
            self._rebuild(cells, order)
            return

        for key, row in cells.items():
            if self._rendered[key] != row:
                for column, value in zip(self._columns, row, strict=True):
                    self.table.update_cell(key, column, value)
                self._rendered[key] = row

        now = time.monotonic()
        settled = (
            now - self._last_reorder >= REORDER_SECONDS and not self.frozen and not self.holding
        )
        if list(order) != self._order and (reorder_now or settled):
            cursor = self.cursor_key()
            rank = {key: i for i, key in enumerate(order)}
            self.table.sort(self._columns[0], key=lambda cell: rank[str(cell)])
            self._order = list(order)
            self._restore_cursor(cursor)
        if reorder_now or settled:
            self._last_reorder = now

    def cursor_key(self) -> str | None:
        if not self.table.row_count:
            return None
        try:
            coordinate = Coordinate(self.table.cursor_row, 0)
            return self.table.coordinate_to_cell_key(coordinate).row_key.value
        except Exception:
            return None

    def _rebuild(self, cells: Mapping[str, tuple[Text, ...]], order: Sequence[str]) -> None:
        cursor = self.cursor_key()
        self.table.clear()
        self._rendered = {}
        self._order = [key for key in order if key in cells]
        for key in self._order:
            self.table.add_row(*cells[key], key=key)
            self._rendered[key] = cells[key]
        self._restore_cursor(cursor)
        self._last_reorder = time.monotonic()

    def _restore_cursor(self, key: str | None) -> None:
        if not self._order:
            return
        if self._follow_cursor and key is not None and key in self._rendered:
            self.table.move_cursor(row=self.table.get_row_index(key), scroll=True)
        else:
            self.table.move_cursor(row=0, scroll=True)
