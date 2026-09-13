"""
Chartink tab: saved Chartink screeners and dashboard widgets, their last
results, and swingdash's own columns (price band, Burst Power) beside them.

Left, a tree: standalone screeners, then one branch per imported dashboard.
Right, the highlighted item's last result. Nothing talks to Chartink until
you add something (a) or run it (R); results are kept, so reopening the tab
shows the last ones straight away.

Results are small and change only when run, so the table is rebuilt when
the rows, filter or sort change, and just the Band/Burst cells update while
their data fills in.
"""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import Input, Static, Tree
from textual.widgets.tree import TreeNode

from swingdash.domain.chartink import (
    ChartinkInputError,
    ChartinkItem,
    ChartinkKind,
    ChartinkRequest,
    DashboardDef,
    ImportTarget,
    ScreenerDef,
    WidgetDef,
)
from swingdash.domain.securities import PriceBand
from swingdash.services.chartink import ChartinkView, EnrichedRow, RunStatus
from swingdash.services.watchlists import WatchlistExistsError
from swingdash.ui.export import ExportTable
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.chartink import cells
from swingdash.ui.tabs.chartink.add_modal import AddChartinkModal
from swingdash.ui.tabs.chartink.dashboard_picker import DashboardPicker
from swingdash.ui.watchlist.confirm_modal import ConfirmModal
from swingdash.ui.widgets.nav_table import NavTable
from swingdash.ui.widgets.prompt_modal import PromptModal

if TYPE_CHECKING:
    from swingdash.ui.app import SwingDashApp

# Tree node data: what a node stands for.
NodeRef = tuple[str, int | str]
SCREENERS: NodeRef = ("screeners", "")

_EVENT_LOG_SIZE = 3
# Narrowest band first, "no band" widest.
_BAND_RANK = {band: rank for rank, band in enumerate(PriceBand)}


def _item_ref(item_id: int) -> NodeRef:
    return ("item", item_id)


def _collection_ref(name: str) -> NodeRef:
    return ("collection", name)


class ChartinkTab(TabBase):
    REFRESH_HZ = 2.0

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "add_item", "Add"),
        Binding("R", "run_selected", "Run"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "reverse_sort", "Reverse"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("W", "save_watchlist", "Save as watchlist"),
        Binding("m", "rename_selected", "Rename"),
        Binding("D", "delete_selected", "Delete"),
    ]

    def __init__(self) -> None:
        super().__init__(id="chartink-view")
        self._selected: NodeRef | None = None
        self._collapsed: set[NodeRef] = set()
        self._tree_shape: tuple[object, ...] | None = None
        self._tree_stamp: tuple[int, RunStatus] | None = None
        self._leaves: dict[NodeRef, TreeNode[NodeRef]] = {}

        self._view: ChartinkView | None = None
        self._view_stamp: tuple[object, ...] | None = None

        self._filter = ""
        self._sort: str | None = None  # None: Chartink's own order
        self._descending = False
        self._signature: tuple[object, ...] | None = None
        self._order: list[tuple[int, EnrichedRow]] = []
        self._extras: dict[str, tuple[object, ...]] = {}

        self._importing: str | None = None
        self._event_log: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="chartink-body"):
            tree: Tree[NodeRef] = Tree("Chartink", id="chartink-tree")
            tree.show_root = False
            yield tree
            with Vertical(id="chartink-results"):
                yield Static(id="chartink-header")
                yield NavTable(id="chartink-table", cursor_type="row", zebra_stripes=True)
                yield Input(placeholder="filter rows...", id="chartink-filter", classes="hidden")
                yield Static(id="chartink-status")

    # --- TabBase hooks -------------------------------------------------------

    def on_tab_mount(self) -> None:
        self._item_tree = cast("Tree[NodeRef]", self.query_one("#chartink-tree", Tree))
        self._table = self.query_one("#chartink-table", NavTable)
        self._header_line = self.query_one("#chartink-header", Static)
        self._filter_input = self.query_one("#chartink-filter", Input)
        self._status_line = self.query_one("#chartink-status", Static)
        self._item_tree.focus()

    def activate(self) -> None:
        super().activate()
        if hasattr(self, "_item_tree") and not self._table.has_focus:
            self._item_tree.focus()

    def refresh_view(self) -> None:
        chartink = self.services.chartink
        self._event_log.extend(chartink.events())
        del self._event_log[:-_EVENT_LOG_SIZE]
        status = chartink.status
        stamp = (chartink.version, status)
        if stamp != self._tree_stamp:
            self._sync_tree(status)
            self._tree_stamp = stamp
        self._view = self._current_view()
        self._draw_table()
        self._header_line.update(self._header_text(status))
        self._status_line.update(self._status_text())

    def export_data(self) -> ExportTable | None:
        view = self._view
        if view is None or view.item.result is None or not self._order:
            return None
        result = view.item.result
        columns = cells.data_columns(result)
        headers = [cells.key_header(result), *(cells.header(c) for c in columns)]
        if view.enriched:
            headers += ["BAND", "BURST POWER"]
        rows: list[list[object]] = []
        for _, enriched in self._order:
            row = enriched.row
            values: list[object] = [cells.key_text(row.key), *(row.values.get(c) for c in columns)]
            if view.enriched:
                values.append(enriched.band.label if enriched.band else None)
                values.append(enriched.burst.power_score if enriched.burst else None)
            rows.append(values)
        slug = re.sub(r"[^\w-]+", "-", view.item.name).strip("-").lower() or "results"
        return ExportTable(name=f"chartink-{slug}", headers=headers, rows=rows)

    def chart_symbol(self) -> str | None:
        enriched = self._cursor_row()
        return enriched.symbol if enriched else None

    # --- tree ------------------------------------------------------------------

    def _sync_tree(self, status: RunStatus) -> None:
        items = self.services.chartink.items()
        shape = tuple((item.collection, item.id) for item in items)
        if shape != self._tree_shape:
            self._tree_shape = shape
            self._rebuild_tree(items)
        for item in items:
            node = self._leaves.get(_item_ref(item.id))
            if node is not None:
                node.set_label(self._item_label(item, status))
        screeners = self._leaves.get(SCREENERS)
        if screeners is not None:
            count = sum(1 for item in items if item.collection is None)
            screeners.set_label(Text.assemble(("Screeners", "bold"), (f"  {count}", "grey50")))
        for collection in {item.collection for item in items if item.collection}:
            node = self._leaves.get(_collection_ref(collection))
            if node is not None:
                node.set_label(self._collection_label(collection, items, status))

    def _rebuild_tree(self, items: list[ChartinkItem]) -> None:
        tree = self._item_tree
        # Remember what the user collapsed, so a rebuild doesn't reopen it.
        for ref, node in self._leaves.items():
            if node.allow_expand and not node.is_expanded:
                self._collapsed.add(ref)
            elif node.allow_expand:
                self._collapsed.discard(ref)

        tree.clear()
        self._leaves = {}
        screeners = tree.root.add("Screeners", data=SCREENERS)
        self._leaves[SCREENERS] = screeners
        branches: dict[str, TreeNode[NodeRef]] = {}
        for item in items:
            if item.collection is None:
                parent = screeners
            else:
                parent = branches.get(item.collection)
                if parent is None:
                    ref = _collection_ref(item.collection)
                    parent = tree.root.add(item.collection, data=ref)
                    branches[item.collection] = parent
                    self._leaves[ref] = parent
            ref = _item_ref(item.id)
            self._leaves[ref] = parent.add_leaf(item.name, data=ref)
        for ref, node in self._leaves.items():
            if node.allow_expand and ref not in self._collapsed:
                node.expand()

        target = self._leaves.get(self._selected) if self._selected else None
        if target is None:
            # The selection went away (deleted) or never existed: first item, else the top.
            first = next((r for r in self._leaves if r[0] == "item"), SCREENERS)
            target = self._leaves[first]
        self._move_tree_cursor(target)

    def _move_tree_cursor(self, node: TreeNode[NodeRef]) -> None:
        tree = self._item_tree
        parent = node.parent
        if parent is not None and not parent.is_expanded:
            parent.expand()
        _ = tree.last_line  # lay out the new nodes so they have line numbers
        # Reset first: a node on the same line as the old cursor wouldn't
        # otherwise become the cursor node.
        tree.move_cursor(None)
        tree.move_cursor(node)
        # Set directly: callers are mid-refresh or refresh right after.
        self._selected = node.data

    def select_item(self, item_id: int) -> None:
        """Highlight an item; a tree rebuild that adds it keeps the selection."""
        self._selected = _item_ref(item_id)
        node = self._leaves.get(self._selected)
        if node is not None:
            self._move_tree_cursor(node)
        self._safe_refresh()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[NodeRef]) -> None:
        # Ignore stale highlights from nodes a rebuild has since replaced.
        if event.node is self._item_tree.cursor_node:
            self._select(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[NodeRef]) -> None:
        # Enter on an item jumps to its results.
        if event.node.data and event.node.data[0] == "item" and self._table.row_count:
            self._table.focus()

    def _select(self, ref: NodeRef | None) -> None:
        if ref != self._selected:
            self._selected = ref
            self._safe_refresh()

    def _item_label(self, item: ChartinkItem, status: RunStatus) -> Text:
        label = Text(item.name)
        if item.id == status.running_id:
            label.append("  running", style="cyan")
        elif item.id in status.queued:
            label.append("  queued", style="cyan")
        elif item.error:
            label.append("  !", style="bold red")
        if item.result is not None:
            label.append(f"  {len(item.result.rows)}", style="grey50")
        return label

    def _collection_label(
        self, collection: str, items: list[ChartinkItem], status: RunStatus
    ) -> Text:
        members = [item for item in items if item.collection == collection]
        label = Text(collection, style="bold")
        busy = any(i.id == status.running_id or i.id in status.queued for i in members)
        label.append(f"  {len(members)}", style="grey50")
        if busy:
            label.append("  running", style="cyan")
        return label

    def _selected_item(self) -> ChartinkItem | None:
        if self._selected and self._selected[0] == "item":
            return self.services.chartink.get(int(self._selected[1]))
        return None

    def _selected_items(self) -> list[ChartinkItem]:
        """The item, or everything under the selected branch."""
        chartink = self.services.chartink
        if self._selected is None:
            return []
        kind, value = self._selected
        if kind == "item":
            item = chartink.get(int(value))
            return [item] if item else []
        if kind == "collection":
            return chartink.collection_items(str(value))
        return [item for item in chartink.items() if item.collection is None]

    # --- actions -----------------------------------------------------------------

    def action_add_item(self) -> None:
        self.app.push_screen(AddChartinkModal(), self._added)

    def _added(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        name, text = result
        chartink = self.services.chartink
        try:
            parsed = chartink.parse(text)
        except ChartinkInputError as exc:
            self.app.notify(str(exc), severity="error", timeout=8)
            return
        if isinstance(parsed, ChartinkRequest):
            self._add_and_run(name or self._default_name(parsed), parsed)
        else:
            self._start_import(parsed, name)

    def _default_name(self, request: ChartinkRequest) -> str:
        kind = "Screener" if request.kind is ChartinkKind.SCREENER else "Widget"
        return f"{kind} {len(self.services.chartink.items()) + 1}"

    def _add_and_run(self, name: str, request: ChartinkRequest) -> None:
        item = self.services.chartink.add(name, request)
        self.services.chartink.run([item.id])
        self.select_item(item.id)
        self.app.notify(f"Added '{item.name}' - running it on Chartink...")

    def _start_import(self, target: ImportTarget, name: str) -> None:
        if self._importing is not None:
            self.app.notify("An import is already in progress.", severity="warning")
            return
        self._importing = target.url
        self.app.notify(f"Reading {target.url} ...")
        fetch = self._fetch_screener if target.kind == "screener" else self._fetch_dashboard
        self.run_worker(
            partial(fetch, target.url, name), thread=True, exit_on_error=False, group="import"
        )

    def _fetch_screener(self, url: str, name: str) -> None:
        """Worker thread."""
        try:
            screener = self.services.chartink.fetch_screener(url)
        except Exception as exc:
            self.app.call_from_thread(self._import_failed, exc)
            return
        self.app.call_from_thread(self._screener_fetched, screener, url, name)

    def _fetch_dashboard(self, url: str, name: str) -> None:
        """Worker thread."""
        del name  # widgets keep their own names; the dashboard names the branch
        try:
            dashboard = self.services.chartink.fetch_dashboard(url)
        except Exception as exc:
            self.app.call_from_thread(self._import_failed, exc)
            return
        self.app.call_from_thread(self._dashboard_fetched, dashboard, url)

    def _import_failed(self, exc: Exception) -> None:
        self._importing = None
        self.app.notify(f"Import failed: {exc}", severity="error", timeout=10)

    def _screener_fetched(self, screener: ScreenerDef, url: str, name: str) -> None:
        self._importing = None
        chartink = self.services.chartink
        try:
            item = chartink.add_screener(screener, url, name or None)
        except ChartinkInputError as exc:
            self.app.notify(str(exc), severity="error", timeout=10)
            return
        chartink.run([item.id])
        self.select_item(item.id)
        self.app.notify(f"Added screener '{item.name}' - running it...")

    def _dashboard_fetched(self, dashboard: DashboardDef, url: str) -> None:
        self._importing = None
        if not dashboard.widgets:
            self.app.notify(
                f"'{dashboard.name}' has no widgets of its own to import"
                + (" (it's private)." if dashboard.is_private else "."),
                severity="warning",
                timeout=8,
            )
            return
        self.app.push_screen(
            DashboardPicker(dashboard), partial(self._widgets_picked, dashboard, url)
        )

    def _widgets_picked(
        self, dashboard: DashboardDef, url: str, widgets: list[WidgetDef] | None
    ) -> None:
        if not widgets:
            return
        chartink = self.services.chartink
        items = chartink.add_widgets(dashboard, widgets, url)
        if not items:
            return
        chartink.run([item.id for item in items])
        self.select_item(items[0].id)
        self.app.notify(
            f"Imported {len(items)} widget(s) from '{dashboard.name}' - running them one at a time..."
        )

    def action_run_selected(self) -> None:
        items = self._selected_items()
        if not items:
            self.app.notify(
                "Nothing to run - press a to add a Chartink link or payload.", severity="warning"
            )
            return
        added = self.services.chartink.run([item.id for item in items])
        if not added:
            self.app.notify("Already running.", severity="warning")
        elif len(items) == 1:
            self.app.notify(f"Running '{items[0].name}' on Chartink...")
        else:
            self.app.notify(f"Running {added} items on Chartink, one at a time...")
        self._safe_refresh()

    def action_rename_selected(self) -> None:
        if self._selected is None or self._selected == SCREENERS:
            self.app.notify("Select a screener, widget or dashboard to rename.", severity="warning")
            return
        kind, value = self._selected
        if kind == "collection":
            self.app.push_screen(
                PromptModal("Rename dashboard", str(value), "Rename"),
                partial(self._renamed_collection, str(value)),
            )
            return
        item = self._selected_item()
        if item is not None:
            self.app.push_screen(
                PromptModal("Rename", item.name, "Rename"), partial(self._renamed_item, item.id)
            )

    def _renamed_item(self, item_id: int, name: str | None) -> None:
        if name:
            self.services.chartink.rename(item_id, name)
            self._safe_refresh()

    def _renamed_collection(self, old: str, name: str | None) -> None:
        if name and name != old:
            renamed = self.services.chartink.rename_collection(old, name)
            self._selected = _collection_ref(renamed)
            self._safe_refresh()

    def action_delete_selected(self) -> None:
        if self._selected is None or self._selected == SCREENERS:
            self.app.notify("Select a screener, widget or dashboard to delete.", severity="warning")
            return
        kind, value = self._selected
        if kind == "collection":
            count = len(self.services.chartink.collection_items(str(value)))
            message = f"Delete dashboard '{value}' and its {count} widget(s)?"
        else:
            item = self._selected_item()
            if item is None:
                return
            message = f"Delete '{item.name}'?"
        self.app.push_screen(
            ConfirmModal(message + "\nThe Chartink original is not affected."),
            partial(self._confirm_delete, self._selected),
        )

    def _confirm_delete(self, ref: NodeRef, confirmed: bool | None) -> None:
        if not confirmed:
            return
        kind, value = ref
        if kind == "collection":
            self.services.chartink.delete_collection(str(value))
        else:
            self.services.chartink.delete(int(value))
        self._selected = None
        self._safe_refresh()

    def action_save_watchlist(self) -> None:
        item = self._selected_item()
        symbols = self.services.chartink.symbols(item.id) if item else []
        if item is None or not symbols:
            self.app.notify(
                "Select a result that lists NSE stocks to save it as a watchlist.",
                severity="warning",
            )
            return
        self.app.push_screen(
            PromptModal(f"Save {len(symbols)} symbols as the watchlist", item.name, "Save"),
            partial(self._watchlist_named, symbols),
        )

    def _watchlist_named(self, symbols: list[str], name: str | None) -> None:
        if not name:
            return
        existing = self.services.watchlists.get(name)
        if existing is None:
            self._save_watchlist(name, symbols, replace=False, confirmed=True)
            return
        self.app.push_screen(
            ConfirmModal(
                f"Watchlist '{name}' already exists ({len(existing.symbols)} symbols).\n"
                f"Replace it with these {len(symbols)}?",
                confirm_label="Replace",
            ),
            partial(self._save_watchlist, name, symbols, True),
        )

    def _save_watchlist(
        self, name: str, symbols: list[str], replace: bool, confirmed: bool | None
    ) -> None:
        if not confirmed:
            return
        try:
            self.services.watchlists.save(name, symbols, replacing=name if replace else None)
        except WatchlistExistsError as exc:
            self.app.notify(str(exc), severity="error")
            return
        cast("SwingDashApp", self.app).show_watchlist(name)
        self.app.notify(f"Saved '{name}' ({len(symbols)} symbols) - now the active watchlist.")

    def action_cycle_sort(self) -> None:
        options = self._sort_options()
        position = options.index(self._sort) if self._sort in options else 0
        self._sort = options[(position + 1) % len(options)]
        # Text reads best A-Z; numbers and scores biggest first.
        self._descending = self._sort not in (None, cells.KEY_COLUMN)
        self._safe_refresh()

    def action_reverse_sort(self) -> None:
        if self._sort is not None:
            self._descending = not self._descending
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
        if event.input.id == "chartink-filter":
            self._filter = event.value.strip().upper()
            self._safe_refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chartink-filter":
            self._filter_input.add_class("hidden")
            self._table.focus()

    # --- results table -----------------------------------------------------------

    def _current_view(self) -> ChartinkView | None:
        item_ref = self._selected if self._selected and self._selected[0] == "item" else None
        chartink = self.services.chartink
        stamp = (item_ref, chartink.version, self.services.securities.snapshot().version)
        if stamp != self._view_stamp:
            self._view_stamp = stamp
            return chartink.view(int(item_ref[1])) if item_ref else None
        return self._view

    def _sort_options(self) -> list[str | None]:
        view = self._view
        if view is None or view.item.result is None:
            return [None]
        options: list[str | None] = [None, cells.KEY_COLUMN, *cells.data_columns(view.item.result)]
        if view.enriched:
            options += [cells.BAND_COLUMN, cells.BURST_COLUMN]
        return options

    def _visible_rows(self, view: ChartinkView) -> list[tuple[int, EnrichedRow]]:
        """(position in the result, row) - filtered and sorted as shown."""
        rows = list(enumerate(view.rows))
        if self._filter:
            needle = self._filter
            rows = [
                (i, r)
                for i, r in rows
                if needle in r.row.key.upper()
                or any(isinstance(v, str) and needle in v.upper() for v in r.row.values.values())
            ]
        if self._sort is None:
            return rows[::-1] if self._descending else rows
        keyed = [(pair, self._sort_value(pair[1])) for pair in rows]
        present = [(pair, value) for pair, value in keyed if value is not None]
        present.sort(key=lambda entry: cells.sort_key(entry[1]), reverse=self._descending)
        return [pair for pair, _ in present] + [pair for pair, value in keyed if value is None]

    def _sort_value(self, row: EnrichedRow) -> object:
        if self._sort == cells.KEY_COLUMN:
            return row.row.key
        if self._sort == cells.BAND_COLUMN:
            return _BAND_RANK[row.band] if row.band else None
        if self._sort == cells.BURST_COLUMN:
            return row.burst.power_score if row.burst else None
        value = row.row.values.get(self._sort or "")
        return None if value == "" else value

    def _draw_table(self) -> None:
        view, table = self._view, self._table
        result = view.item.result if view else None
        if view is None or result is None:
            if self._signature is not None or table.row_count:
                table.clear(columns=True)
            self._signature, self._order, self._extras = None, [], {}
            return

        if self._sort not in self._sort_options():
            self._sort, self._descending = None, False
        extras = tuple(self._extra(r) for r in view.rows)
        signature = (
            view.item.id,
            view.item.fetched_at,
            result.columns,
            view.enriched,
            self._filter,
            self._sort,
            self._descending,
            # Band/Burst values only reorder rows when sorting by them.
            extras if self._sort in (cells.BAND_COLUMN, cells.BURST_COLUMN) else None,
        )
        if signature != self._signature:
            self._rebuild_table(view)
            self._signature = signature
            return
        if not view.enriched:
            return
        # Same rows in the same order: fill in Band/Burst as they arrive.
        for index, enriched in enumerate(view.rows):
            row_key, extra = str(index), extras[index]
            if row_key in self._extras and self._extras[row_key] != extra:
                table.update_cell(row_key, cells.BAND_COLUMN, cells.band_cell(enriched.band))
                table.update_cell(row_key, cells.BURST_COLUMN, cells.burst_cell(enriched.burst))
                self._extras[row_key] = extra
        self._order = self._visible_rows(view)  # so export sees the new values

    @staticmethod
    def _extra(row: EnrichedRow) -> tuple[object, ...]:
        burst = row.burst
        return (
            row.band,
            burst.power_score if burst else None,
            burst.classification if burst else None,
        )

    def _rebuild_table(self, view: ChartinkView) -> None:
        table = self._table
        result = view.item.result
        assert result is not None
        cursor_key = self._cursor_key()
        table.clear(columns=True)

        columns = cells.data_columns(result)
        arrow = " v" if self._descending else " ^"

        def add(key: str, label: str) -> None:
            table.add_column(label + (arrow if key == self._sort else ""), key=key)

        add(cells.KEY_COLUMN, cells.key_header(result))
        for column in columns:
            add(column, cells.header(column))
        if view.enriched:
            add(cells.BAND_COLUMN, "BAND")
            add(cells.BURST_COLUMN, "BURST")

        self._order = self._visible_rows(view)
        self._extras = {}
        for index, enriched in self._order:
            row = enriched.row
            row_key = str(index)
            row_cells = [
                cells.key_cell(row.key, enriched.symbol is not None),
                *(cells.value_cell(c, row.values.get(c)) for c in columns),
            ]
            if view.enriched:
                row_cells += [cells.band_cell(enriched.band), cells.burst_cell(enriched.burst)]
                self._extras[row_key] = self._extra(enriched)
            table.add_row(*row_cells, key=row_key)

        if table.row_count:
            keys = {str(index) for index, _ in self._order}
            row = table.get_row_index(cursor_key) if cursor_key in keys else 0
            table.move_cursor(row=row, scroll=True)

    def _cursor_key(self) -> str | None:
        table = self._table
        if not table.row_count:
            return None
        try:
            return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _cursor_row(self) -> EnrichedRow | None:
        view, key = self._view, self._cursor_key()
        if view is None or key is None or not key.isdigit() or int(key) >= len(view.rows):
            return None
        return view.rows[int(key)]

    # --- header and status -------------------------------------------------------

    def _header_text(self, status: RunStatus) -> Text:
        text = Text()
        items = self.services.chartink.items()
        if not items:
            text.append("No saved Chartink screeners or dashboards yet - ", style="grey62")
            text.append("press a", style="yellow")
            text.append(
                " and paste a chartink.com link, a request payload or a scan clause.",
                style="grey62",
            )
            return text

        view = self._view
        if view is None:
            members = self._selected_items()
            if self._selected and self._selected[0] == "collection":
                text.append(str(self._selected[1]), style="bold")
            else:
                text.append("Screeners", style="bold")
            text.append(f"  {len(members)} item(s)  ", style="grey62")
            if members:
                text.append("R runs them all, one at a time", style="yellow")
            return text

        item = view.item
        text.append(item.name, style="bold")
        kind = "screener" if item.request.kind is ChartinkKind.SCREENER else "widget"
        text.append(f"  {kind}", style="grey62")
        if item.collection:
            text.append(f" in {item.collection}", style="grey62")
        text.append("  ")
        if item.id == status.running_id:
            text.append("running...  ", style="cyan")
        elif item.id in status.queued:
            text.append("queued...  ", style="cyan")

        result = item.result
        if result is None:
            if item.error is None and not status.busy:
                text.append("not run yet - press R", style="yellow")
        else:
            shown = len(self._order)
            total = len(result.rows)
            counts = f"{shown}/{total} rows" if shown != total else f"{total} rows"
            if result.available is not None and result.available > total:
                counts += f" of {result.available:,}"
            text.append(counts + "  ", style="grey62")
            if item.fetched_at is not None:
                text.append(f"fetched {item.fetched_at:%a %d %b %H:%M}  ", style="grey62")
            if result.data_time is not None:
                text.append(f"data {result.data_time:%H:%M}  ", style="grey62")
            text.append("Chartink data may be delayed ~5 min", style="grey50")
        if item.error:
            prefix = "last run failed (showing the previous result): " if result else "failed: "
            text.append(f"\n{prefix}{item.error}", style="red")
        return text

    def _status_text(self) -> Text:
        text = Text()
        view = self._view
        if self._importing:
            text.append(f"importing {self._importing} ...  ", style="cyan")
        if view is not None and view.item.result is not None:
            if self._sort is None:
                text.append(
                    f"Chartink order{' (reversed)' if self._descending else ''}  ", style="cyan"
                )
            else:
                label = {
                    cells.KEY_COLUMN: cells.key_header(view.item.result),
                    cells.BAND_COLUMN: "BAND",
                    cells.BURST_COLUMN: "BURST",
                }.get(self._sort, cells.header(self._sort))
                text.append(
                    f"sort {label.lower()}{'v' if self._descending else '^'}  ", style="cyan"
                )
            if self._filter:
                text.append(f"/{self._filter}  ", style="yellow")
            if view.enriched and not view.bands_available:
                text.append("bands: fetch them once in the Securities tab (R)  ", style="yellow")
            if view.history_pending:
                text.append("Burst Power loading history...  ", style="yellow")
            elif not view.item.result.is_stock_list:
                text.append("not a stock list - no Band/Burst columns  ", style="grey50")
        if self._event_log:
            text.append(" | ".join(self._event_log[-2:]), style="yellow")
        return text
