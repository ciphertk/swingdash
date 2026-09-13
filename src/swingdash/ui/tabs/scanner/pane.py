"""
Scanner tab: Burst Power and Mswing for the active watchlist, one row per
symbol, with a detail panel for the highlighted row.

All numbers come from ScannerEngine snapshots (see services/scanner); this
module only renders them. The table is kept in step by LiveTable, so
hundreds of rows redraw without rebuilding.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.events import Key
from textual.widgets import DataTable, Input, Static
from textual.widgets.data_table import ColumnKey

from swingdash.domain.scanner import ScannerRow, ScannerSnapshot
from swingdash.domain.watchlist import Watchlist
from swingdash.services.scanner.engine import ScannerEngine
from swingdash.ui.export import ExportTable
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.scanner.columns import COLUMNS, DEFAULT_SORT, SORTABLE
from swingdash.ui.tabs.scanner.detail import burst_panel, mswing_panel
from swingdash.ui.tabs.scanner.index_picker import IndexPicker
from swingdash.ui.widgets.live_table import LiveTable
from swingdash.ui.widgets.nav_table import NavTable

_NAV_KEYS = {"up", "down", "pageup", "pagedown", "home", "end", "g", "G"}


class ScannerTab(TabBase):
    uses_watchlist = True
    REFRESH_HZ = 2.0

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "reverse_sort", "Reverse"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("f", "toggle_freeze", "Freeze"),
        Binding("i", "pick_index", "Index"),
        Binding("p", "toggle_panel", "Panel"),
    ]

    def __init__(self) -> None:
        super().__init__(id="scanner-view")
        self._engine: ScannerEngine | None = None
        self._sort_index = DEFAULT_SORT
        self._descending = SORTABLE[DEFAULT_SORT].descending_first
        self._filter = ""
        self._event_log: list[str] = []
        self._rows: dict[str, ScannerRow] = {}
        self._snapshot: ScannerSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield NavTable(id="scanner-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="scanner-detail"):
            yield Static(id="scanner-burst")
            yield Static(id="scanner-mswing")
        yield Input(placeholder="filter symbols...", id="scanner-filter", classes="hidden")
        yield Static(id="scanner-status")

    # --- TabBase hooks -------------------------------------------------------

    def on_tab_mount(self) -> None:
        table = self.query_one("#scanner-table", NavTable)
        for column in COLUMNS:
            table.add_column(column.header, key=column.key, width=column.width)
        self._live = LiveTable(table, [c.key for c in COLUMNS])
        self._detail = self.query_one("#scanner-detail", Horizontal)
        self._burst_panel = self.query_one("#scanner-burst", Static)
        self._mswing_panel = self.query_one("#scanner-mswing", Static)
        self._filter_input = self.query_one("#scanner-filter", Input)
        self._status_line = self.query_one("#scanner-status", Static)
        table.focus()

    def on_watchlist_changed(self, watchlist: Watchlist | None) -> None:
        symbols = list(watchlist.symbols) if watchlist else []
        if self._engine is None:
            self._engine = self.services.new_scanner_engine(symbols)
            self._engine.start()
        else:
            self._engine.set_symbols(symbols)
        self._live.reset()

    def activate(self) -> None:
        super().activate()
        if hasattr(self, "_live"):
            self._live.table.focus()

    def on_unmount(self) -> None:
        if self._engine is not None:
            self._engine.stop()

    def export_data(self) -> ExportTable | None:
        rows = [self._rows[key] for key in self._live.order if key in self._rows]
        if not rows:
            return None
        return ExportTable(
            name="scanner",
            headers=_EXPORT_HEADERS,
            rows=[_export_row(r, self._snapshot) for r in rows],
        )

    def chart_symbol(self) -> str | None:
        return self._live.cursor_key()

    # --- actions -------------------------------------------------------------

    def action_cycle_sort(self) -> None:
        self._sort_index = (self._sort_index + 1) % len(SORTABLE)
        self._descending = SORTABLE[self._sort_index].descending_first
        self._redraw(reorder_now=True)

    def action_reverse_sort(self) -> None:
        self._descending = not self._descending
        self._redraw(reorder_now=True)

    def action_toggle_freeze(self) -> None:
        self._live.frozen = not self._live.frozen
        self._safe_refresh()

    def action_toggle_panel(self) -> None:
        self._detail.display = not self._detail.display

    def action_focus_filter(self) -> None:
        self._filter_input.remove_class("hidden")
        self._filter_input.focus()

    def action_clear_filter(self) -> None:
        self._filter_input.value = ""
        self._filter = ""
        self._filter_input.add_class("hidden")
        self._live.table.focus()
        self._safe_refresh()

    def action_pick_index(self) -> None:
        if self._engine is None:
            return
        indices = [
            (index["instrument_key"], index.get("trading_symbol") or index.get("name", ""))
            for index in self.services.instruments.list_indices()
        ]
        if not indices:
            self.app.notify(
                "Index list not downloaded - run `swingdash setup`.", severity="warning"
            )
            return
        self.app.push_screen(IndexPicker(indices, self._engine.index_key), self._index_picked)

    def _index_picked(self, key: str | None) -> None:
        if key is None or self._engine is None or key == self._engine.index_key:
            return
        name = self.services.instruments.index_name(key)
        self.services.preferences.set_scanner_index(key)
        self._engine.set_index(key, name)
        self.app.notify(f"Mswing now compares against {name}.")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "scanner-filter":
            self._filter = event.value.strip().upper()
            self._safe_refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scanner-filter":
            self._filter_input.add_class("hidden")
            self._live.table.focus()

    def on_key(self, event: Key) -> None:
        if event.key in _NAV_KEYS and self._live.table.has_focus:
            self._live.navigated()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    # --- rendering -----------------------------------------------------------

    def refresh_view(self) -> None:
        if self._engine is None:
            return
        self._event_log.extend(self._engine.events())
        del self._event_log[:-3]
        self._redraw()

    def _redraw(self, reorder_now: bool = False) -> None:
        if self._engine is None:
            return
        snapshot = self._engine.snapshot()
        self._snapshot = snapshot
        self._rows = {
            row.symbol: row
            for row in snapshot.rows
            if not self._filter or self._filter in row.symbol
        }
        cells = {
            key: tuple(column.cell(row) for column in COLUMNS) for key, row in self._rows.items()
        }
        self._live.render(cells, self._sorted_keys(), reorder_now=reorder_now)
        self._update_headers()
        self._update_detail()
        self._status_line.update(self._status(snapshot))

    def _sorted_keys(self) -> list[str]:
        sort = SORTABLE[self._sort_index].sort
        assert sort is not None
        present = [k for k, row in self._rows.items() if sort(row) is not None]
        missing = sorted(k for k, row in self._rows.items() if sort(row) is None)
        present.sort(key=lambda k: sort(self._rows[k]), reverse=self._descending)  # type: ignore[arg-type,return-value]
        return present + missing

    def _update_headers(self) -> None:
        active = SORTABLE[self._sort_index].key
        arrow = " v" if self._descending else " ^"
        table = self._live.table
        for column in COLUMNS:
            label = table.columns.get(ColumnKey(column.key))
            if label is not None:
                label.label = Text(column.header + (arrow if column.key == active else ""))

    def _update_detail(self) -> None:
        if self._snapshot is None or not self._detail.display:
            return
        key = self._live.cursor_key()
        row = self._rows.get(key) if key else None
        self._burst_panel.update(burst_panel(row))
        self._mswing_panel.update(mswing_panel(row, self._snapshot.index))

    def _status(self, snapshot: ScannerSnapshot) -> Text:
        status = Text()
        if not snapshot.total:
            status.append("no watchlist symbols  ", style="grey62")
            status.append("press 'n' to create a watchlist", style="yellow")
            return status

        now = self.services.calendar.now()
        index_score = snapshot.index.metrics.mswing.score if snapshot.index.metrics else None
        status.append(f"{snapshot.index.name} Mswing ", style="grey62")
        status.append("-" if index_score is None else f"{index_score:+.2f}  ", style="bold")
        if snapshot.session_date is not None and snapshot.session_date != now.date():
            status.append(f"as of {snapshot.session_date:%a %d %b} close  ", style="yellow")
        elif snapshot.session_closed:
            status.append("session closed - today counted  ", style="grey62")
        else:
            status.append("live - Burst Power counts completed days  ", style="cyan")

        column = SORTABLE[self._sort_index]
        status.append(
            f"sort {column.header.lower()}{'v' if self._descending else '^'}  ", style="cyan"
        )
        if self._filter:
            status.append(f"/{self._filter}  ", style="yellow")
        status.append(f"{len(self._rows)}/{snapshot.total} rows  ", style="grey62")
        if snapshot.loaded < snapshot.total or snapshot.fetching:
            status.append(f"history {snapshot.loaded}/{snapshot.total}", style="yellow")
            status.append(" updating...  " if snapshot.fetching else "  ", style="yellow")
        if self._live.frozen:
            status.append("FROZEN  ", style="yellow bold")
        elif self._live.holding:
            status.append("holding  ", style="grey62")
        if self._event_log:
            status.append("   " + " | ".join(self._event_log[-2:]), style="yellow")
        return status


_EXPORT_HEADERS = (
    "SYMBOL",
    "LTP",
    "CHG%",
    "BURST POWER",
    "5%+",
    "10%+",
    "19%+",
    "MAX%",
    "MAX DATE",
    "LAST 5%",
    "LAST 10%",
    "LAST 19%",
    "MSWING",
    "MOMENTUM 20D",
    "MOMENTUM 50D",
    "EMA9",
    "INDEX",
    "INDEX MSWING",
    "VS INDEX",
    "CLASS",
    "HISTORY THROUGH",
    "INCLUDES TODAY",
)


def _export_row(row: ScannerRow, snapshot: ScannerSnapshot | None) -> list[object]:
    metrics = row.metrics
    burst = metrics.burst if metrics else None
    mswing = metrics.mswing if metrics else None
    index = snapshot.index if snapshot else None
    index_score = index.metrics.mswing.score if index and index.metrics else None
    return [
        row.symbol,
        row.ltp,
        row.change_pct,
        burst.power_score if burst else None,
        burst.count_5pct if burst else None,
        burst.count_10pct if burst else None,
        burst.count_19pct if burst else None,
        burst.max_move_pct if burst else None,
        burst.max_move_date if burst else None,
        burst.last_date_5pct if burst else None,
        burst.last_date_10pct if burst else None,
        burst.last_date_19pct if burst else None,
        mswing.score if mswing else None,
        mswing.momentum_short if mswing else None,
        mswing.momentum_long if mswing else None,
        mswing.ema if mswing else None,
        index.name if index else None,
        index_score,
        row.vs_index,
        row.mswing_class,
        row.history_through.isoformat() if row.history_through else None,
        metrics.includes_today if metrics else None,
    ]
