"""
Securities tab: every NSE EQ stock, index and ETF, with price bands,
surveillance stages and sectors. Market-wide - it ignores the watchlist.

The data is end-of-day and refreshed only on request (R). Rendering is
built around DataTable's costs: adding rows measures every cell (~0.5s for
the full stock list), while reordering and updating single cells are cheap.
So a full rebuild happens only when the set of visible rows changes (a
filter, or new data); sorting reorders in place, and sectors arriving
during a backfill update just their cells.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.timer import Timer
from textual.widgets import ContentSwitcher, Input, Static
from textual.widgets.data_table import ColumnKey

from swingdash.domain.securities import PriceBand, SecuritiesSnapshot
from swingdash.ui.export import ExportTable
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.securities.views import BAND_FILTERS, VIEWS, View
from swingdash.ui.widgets.nav_table import NavTable

FILTER_DEBOUNCE_SECONDS = 0.25
NEWER_DATA_CHECK_SECONDS = 60.0


@dataclass
class _ViewState:
    sort_index: int = 0
    descending: bool = False
    order: list[str] = field(default_factory=list)
    rendered: dict[str, tuple[Text, ...]] = field(default_factory=dict)
    # What the table currently shows; re-rendered when this goes stale.
    signature: tuple[object, ...] | None = None


class SecuritiesTab(TabBase):
    REFRESH_HZ = 1.0

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("v", "cycle_view", "View"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "reverse_sort", "Reverse"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("b", "cycle_band", "Band"),
        Binding("m", "toggle_flagged", "Surveillance"),
        Binding("R", "refresh_data", "Refresh data"),
    ]

    def __init__(self) -> None:
        super().__init__(id="securities-view")
        self._view_index = 0
        self._states = {view.id: _ViewState() for view in VIEWS}
        self._filter = ""
        self._band_index = 0
        self._flagged_only = False
        self._debounce: Timer | None = None
        self._newer_data = False
        self._newer_checked_at = float("-inf")

    def compose(self) -> ComposeResult:
        yield Static(id="securities-views")
        with ContentSwitcher(initial=f"securities-{VIEWS[0].id}", id="securities-switcher"):
            for view in VIEWS:
                yield NavTable(id=f"securities-{view.id}", cursor_type="row", zebra_stripes=True)
        yield Input(
            placeholder="filter symbol, name, sector...", id="securities-filter", classes="hidden"
        )
        yield Static(id="securities-status")

    # --- TabBase hooks -------------------------------------------------------

    def on_tab_mount(self) -> None:
        self._switcher = self.query_one("#securities-switcher", ContentSwitcher)
        self._filter_input = self.query_one("#securities-filter", Input)
        self._views_line = self.query_one("#securities-views", Static)
        self._status_line = self.query_one("#securities-status", Static)
        self._tables: dict[str, NavTable] = {}
        for view in VIEWS:
            table = self.query_one(f"#securities-{view.id}", NavTable)
            for column in view.columns:
                table.add_column(column.header, key=column.key, width=column.width)
            self._tables[view.id] = table
        self._table.focus()

    def activate(self) -> None:
        super().activate()
        if hasattr(self, "_tables"):
            self._table.focus()

    def refresh_view(self) -> None:
        snapshot = self.services.securities.snapshot()
        self._draw_table(snapshot)
        self._views_line.update(self._views_text(snapshot))
        self._status_line.update(self._status_text(snapshot))

    def export_data(self) -> ExportTable | None:
        view = self._view
        ordered = self._visible_ordered(self.services.securities.snapshot())
        if not ordered:
            return None
        return ExportTable(
            name=f"securities-{view.id}",
            headers=[column.header for column in view.columns],
            rows=view.values(ordered),
        )

    # --- actions -------------------------------------------------------------

    def action_cycle_view(self) -> None:
        self._view_index = (self._view_index + 1) % len(VIEWS)
        self._switcher.current = f"securities-{self._view.id}"
        self._table.focus()
        self._safe_refresh()

    def action_cycle_sort(self) -> None:
        state = self._state
        state.sort_index = (state.sort_index + 1) % len(self._view.sortable)
        self._safe_refresh()

    def action_reverse_sort(self) -> None:
        self._state.descending = not self._state.descending
        self._safe_refresh()

    def action_cycle_band(self) -> None:
        self._band_index = (self._band_index + 1) % len(BAND_FILTERS)
        self._safe_refresh()

    def action_toggle_flagged(self) -> None:
        self._flagged_only = not self._flagged_only
        self._safe_refresh()

    def action_refresh_data(self) -> None:
        if self.services.securities.refresh():
            self.app.notify("Refreshing NSE securities data...")
            self._newer_checked_at = float("-inf")
        else:
            self.app.notify("A refresh is already running.", severity="warning")
        self._safe_refresh()

    def action_focus_filter(self) -> None:
        self._filter_input.remove_class("hidden")
        self._filter_input.focus()

    def action_clear_filter(self) -> None:
        self._filter_input.value = ""
        self._filter = ""
        self._filter_input.add_class("hidden")
        self._table.focus()
        self._safe_refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "securities-filter":
            return
        # Narrowing 2,300 rows re-measures what's left; wait for a pause in typing.
        if self._debounce is not None:
            self._debounce.stop()
        value = event.value.strip()
        self._debounce = self.set_timer(FILTER_DEBOUNCE_SECONDS, lambda: self._apply_filter(value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "securities-filter":
            if self._debounce is not None:
                self._debounce.stop()
            self._apply_filter(event.value.strip())
            self._filter_input.add_class("hidden")
            self._table.focus()

    def _apply_filter(self, value: str) -> None:
        self._filter = value
        self._safe_refresh()

    # --- rendering -----------------------------------------------------------

    @property
    def _view(self) -> View[Any]:
        return VIEWS[self._view_index]

    @property
    def _state(self) -> _ViewState:
        return self._states[self._view.id]

    @property
    def _table(self) -> NavTable:
        return self._tables[self._view.id]

    @property
    def _band(self) -> PriceBand | None:
        return BAND_FILTERS[self._band_index]

    def _visible_ordered(self, snapshot: SecuritiesSnapshot) -> list[Any]:
        """The active view's rows, filtered and sorted exactly as shown."""
        view, state = self._view, self._state
        rows = view.visible(snapshot, self._filter, self._band, self._flagged_only)
        return view.ordered(rows, view.sortable[state.sort_index], state.descending)

    def _draw_table(self, snapshot: SecuritiesSnapshot) -> None:
        view, state, table = self._view, self._state, self._table
        signature = (
            snapshot.version,
            self._filter,
            self._band,
            self._flagged_only,
            state.sort_index,
            state.descending,
        )
        if signature == state.signature:
            return

        ordered = self._visible_ordered(snapshot)
        keys = [view.key(row) for row in ordered]
        cursor_key = self._cursor_key(table)

        if set(keys) != set(state.rendered):
            table.clear()
            state.rendered = {}
            for key, row in zip(keys, ordered, strict=True):
                cells = view.cells(row)
                table.add_row(*cells, key=key)
                state.rendered[key] = cells
        else:
            for key, row in zip(keys, ordered, strict=True):
                cells = view.cells(row)
                if state.rendered[key] != cells:
                    for column, value in zip(view.columns, cells, strict=True):
                        table.update_cell(key, column.key, value)
                    state.rendered[key] = cells
            if keys != state.order:
                rank = {key: i for i, key in enumerate(keys)}
                table.sort(view.columns[0].key, key=lambda cell: rank[str(cell)])

        state.order = keys
        state.signature = signature
        self._restore_cursor(table, cursor_key, keys)
        self._update_headers(view, state, table)

    def _cursor_key(self, table: NavTable) -> str | None:
        if not table.row_count:
            return None
        try:
            key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        except Exception:
            return None
        return key.value

    def _restore_cursor(self, table: NavTable, cursor_key: str | None, keys: list[str]) -> None:
        if not keys:
            return
        row = table.get_row_index(cursor_key) if cursor_key in set(keys) else 0
        table.move_cursor(row=row, scroll=True)

    def _update_headers(self, view: View[Any], state: _ViewState, table: NavTable) -> None:
        active = view.sortable[state.sort_index].key
        arrow = " v" if state.descending else " ^"
        for column in view.columns:
            label = table.columns.get(ColumnKey(column.key))
            if label is not None:
                label.label = Text(column.header + (arrow if column.key == active else ""))
        table.refresh()

    def _views_text(self, snapshot: SecuritiesSnapshot) -> Text:
        # The footer's "v View" binding already says how to switch - no need
        # to repeat it here too.
        text = Text()
        for index, view in enumerate(VIEWS):
            label = f" {view.title} {len(view.rows(snapshot)):,} "
            text.append(label, style="reverse bold" if index == self._view_index else "grey62")
            text.append(" ")
        return text

    def _status_text(self, snapshot: SecuritiesSnapshot) -> Text:
        securities = self.services.securities
        progress = securities.progress
        status = Text()

        if snapshot.empty and not securities.refreshing:
            status.append("No data yet - ", style="grey62")
            status.append("press R to fetch", style="yellow")
            status.append(
                " (stocks, indices and ETFs take seconds; sectors fill in over ~45 min)",
                style="grey62",
            )
            return status

        view, state = self._view, self._state
        shown = len(state.order)
        status.append(f"{shown:,}/{len(view.rows(snapshot)):,} rows  ", style="grey62")
        arrow = "v" if state.descending else "^"
        status.append(
            f"sort {view.sortable[state.sort_index].header.lower()}{arrow}  ", style="cyan"
        )
        if self._filter:
            status.append(f"/{self._filter}  ", style="yellow")
        if self._band is not None and view.band is not None:
            status.append(f"band {self._band.label}  ", style="yellow")
        if self._flagged_only and view.flagged is not None:
            status.append("surveillance only  ", style="yellow")

        bands = snapshot.datasets.get("bands")
        if bands and bands.as_of:
            status.append(f"bands as of {bands.as_of:%a %d %b}  ", style="grey62")
        if bands and bands.fetched_at:
            status.append(f"fetched {bands.fetched_at:%a %d %b %H:%M}  ", style="grey50")

        if securities.refreshing:
            if progress.phase == "sectors":
                status.append(
                    f"sectors {progress.sectors_done:,}/{progress.sectors_total:,}  ", style="cyan"
                )
            else:
                status.append(f"{progress.phase or 'refreshing'}...  ", style="cyan")
        elif self._newer_data_likely():
            status.append("newer NSE files likely - R to refresh  ", style="yellow")

        failed = sorted(name for name, s in snapshot.datasets.items() if s.error)
        if failed:
            status.append(f"last refresh failed: {', '.join(failed)}", style="red")
        elif progress.error and not securities.refreshing:
            status.append(progress.error[:80], style="red")
        return status

    def _newer_data_likely(self) -> bool:
        # May consult the session calendar; once a minute is plenty for a hint.
        now = time.monotonic()
        if now - self._newer_checked_at >= NEWER_DATA_CHECK_SECONDS:
            self._newer_checked_at = now
            self._newer_data = self.services.securities.newer_data_likely()
        return self._newer_data
