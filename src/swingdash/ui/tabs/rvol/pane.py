"""
Live RVOL tab.

Built on DataTable: arrow-key navigation, scrolling, a row cursor and sticky
headers. Cells are updated in place by row key, so the cursor and scroll
position survive every refresh.

Row ORDER is deliberately not recomputed every frame: values update at
REFRESH_HZ, but ordering settles every REORDER_SECONDS and pauses while you
navigate, so rows never slide out from under the cursor mid-glance.
"""

from __future__ import annotations

import time
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.events import Key
from textual.widgets import Input, Static
from textual.widgets.data_table import ColumnKey

from swingdash.domain.rvol.calc import MODERATE_RATIO, STRONG_RATIO
from swingdash.domain.rvol.types import Snapshot, SymbolRow
from swingdash.domain.watchlist import Watchlist
from swingdash.services.rvol.engine import RvolEngine
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.rvol.table import RvolTable

REORDER_SECONDS = 2.0
# After a navigation keypress, hold the ordering so the row being read stays put.
NAV_FREEZE_SECONDS = 5.0
_NAV_KEYS = {"up", "down", "pageup", "pagedown", "home", "end", "g", "G"}

# (attribute on SymbolRow, column key, header, width)
COLUMNS = [
    ("symbol", "symbol", "SYMBOL", 12),
    ("ltp", "ltp", "LTP", 11),
    ("change_pct", "change_pct", "CHG%", 8),
    ("volume", "volume", "VOLUME", 14),
    ("rvol", "rvol", "RVOL", 9),
    ("rvol_day", "rvol_day", "RVOL-D", 9),
]
SORTABLE = ["rvol", "rvol_day", "change_pct", "volume", "symbol"]


class LiveRvolTab(TabBase):
    uses_watchlist = True
    REFRESH_HZ = 8.0

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "reverse_sort", "Reverse"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("f", "toggle_freeze", "Freeze"),
    ]

    def __init__(self) -> None:
        super().__init__(id="live-rvol-view")
        self._engine: RvolEngine | None = None
        self._sort_index = 0
        self._descending = True
        self._filter = ""
        self._event_log: list[str] = []
        self._rendered: dict[str, tuple[Text, ...]] = {}  # row key -> last cell contents
        self._order: list[str] = []  # row keys, in displayed order
        self._last_reorder = 0.0
        self._nav_until = 0.0
        self._frozen = False
        # Until you move the cursor it stays pinned to the top, so the hottest
        # row is under it; once you navigate it follows that SYMBOL instead.
        self._cursor_follows_symbol = False

    def compose(self) -> ComposeResult:
        yield RvolTable(id="rvol-table", cursor_type="row", zebra_stripes=True)
        yield Input(placeholder="filter symbols...", id="rvol-filter", classes="hidden")
        yield Static(id="rvol-status")

    # --- TabBase hooks -------------------------------------------------------

    def on_tab_mount(self) -> None:
        self._table = self.query_one("#rvol-table", RvolTable)
        self._filter_input = self.query_one("#rvol-filter", Input)
        self._status_line = self.query_one("#rvol-status", Static)
        for _, key, header, width in COLUMNS:
            self._table.add_column(header, key=key, width=width)
        self._table.focus()

    def on_watchlist_changed(self, watchlist: Watchlist | None) -> None:
        symbols = list(watchlist.symbols) if watchlist else []
        if self._engine is None:
            self._engine = self.services.new_rvol_engine(symbols)
            self._engine.start()
        else:
            self._engine.set_symbols(symbols)
        # Row identities changed: drop cached renders so the next frame rebuilds.
        self._rendered.clear()
        self._order = []
        self._cursor_follows_symbol = False

    def activate(self) -> None:
        super().activate()
        if hasattr(self, "_table"):
            self._table.focus()

    def on_unmount(self) -> None:
        if self._engine is not None:
            self._engine.stop()

    # --- actions -------------------------------------------------------------

    def action_cycle_sort(self) -> None:
        self._sort_index = (self._sort_index + 1) % len(SORTABLE)
        self._rebuild(self._visible_rows())

    def action_reverse_sort(self) -> None:
        self._descending = not self._descending
        self._rebuild(self._visible_rows())

    def action_toggle_freeze(self) -> None:
        self._frozen = not self._frozen

    def action_focus_filter(self) -> None:
        self._filter_input.remove_class("hidden")
        self._filter_input.focus()

    def action_clear_filter(self) -> None:
        self._filter_input.value = ""
        self._filter = ""
        self._filter_input.add_class("hidden")
        self._table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "rvol-filter":
            self._filter = event.value.strip().upper()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "rvol-filter":
            # Enter applies the filter and hands focus back so arrows work again.
            self._filter_input.add_class("hidden")
            self._table.focus()

    def on_key(self, event: Key) -> None:
        if event.key in _NAV_KEYS and self._table.has_focus:
            self._nav_until = time.monotonic() + NAV_FREEZE_SECONDS
            self._cursor_follows_symbol = True

    # --- rendering -----------------------------------------------------------

    def refresh_view(self) -> None:
        if self._engine is None:
            return
        self._event_log.extend(self._engine.events())
        del self._event_log[:-3]

        snapshot = self._engine.snapshot()
        rows = self._visible_rows(snapshot)
        if set(rows) != set(self._order):
            self._rebuild(rows)  # filter changed, or symbols resolved
        else:
            self._update_cells(rows)
            self._maybe_reorder(rows)
        self._status_line.update(self._status(snapshot, len(rows)))

    def _visible_rows(self, snapshot: Snapshot | None = None) -> dict[str, SymbolRow]:
        if snapshot is None:
            if self._engine is None:
                return {}
            snapshot = self._engine.snapshot()
        return {
            r.instrument_key: r
            for r in snapshot.rows
            if not self._filter or self._filter in r.symbol
        }

    def _rebuild(self, rows: dict[str, SymbolRow]) -> None:
        """
        Re-add every row in sort order. Rows are keyed, so capturing the
        cursor's key first keeps the selection on the same SYMBOL. Ordering is
        applied by re-adding rows, not DataTable.sort, which would compare the
        styled Text cells rather than numbers.
        """
        cursor_key = self._cursor_key()
        self._table.clear()
        self._rendered.clear()
        self._order = self._sorted_keys(rows)
        for key in self._order:
            cells = self._cells(rows[key])
            self._table.add_row(*cells, key=key)
            self._rendered[key] = cells
        self._restore_cursor(cursor_key)
        self._update_headers()
        self._last_reorder = time.monotonic()

    def _update_cells(self, rows: dict[str, SymbolRow]) -> None:
        for key, row in rows.items():
            cells = self._cells(row)
            if self._rendered.get(key) == cells:
                continue  # nothing changed - skip six no-op writes
            for (_, column_key, _, _), value in zip(COLUMNS, cells, strict=True):
                self._table.update_cell(key, column_key, value)
            self._rendered[key] = cells

    def _maybe_reorder(self, rows: dict[str, SymbolRow]) -> None:
        now = time.monotonic()
        if self._frozen or now < self._nav_until or now - self._last_reorder < REORDER_SECONDS:
            return
        if self._sorted_keys(rows) != self._order:
            self._rebuild(rows)
        self._last_reorder = now

    def _sorted_keys(self, rows: dict[str, SymbolRow]) -> list[str]:
        attr = SORTABLE[self._sort_index]
        # Rows without a value (no baseline or tick yet) always sink to the
        # bottom instead of floating to the top when descending.
        present = [k for k in rows if getattr(rows[k], attr) is not None]
        missing = sorted(k for k in rows if getattr(rows[k], attr) is None)
        present.sort(key=lambda k: getattr(rows[k], attr), reverse=self._descending)
        return present + missing

    def _cursor_key(self) -> str | None:
        if not self._table.row_count:
            return None
        try:
            key = self._table.coordinate_to_cell_key(Coordinate(self._table.cursor_row, 0)).row_key
        except Exception:
            return None
        return key.value

    def _restore_cursor(self, cursor_key: str | None) -> None:
        if not self._cursor_follows_symbol or cursor_key is None or cursor_key not in self._order:
            self._table.move_cursor(row=0, scroll=True)
            return
        self._table.move_cursor(row=self._order.index(cursor_key), scroll=True)

    def _update_headers(self) -> None:
        active = SORTABLE[self._sort_index]
        arrow = " v" if self._descending else " ^"
        for _, key, header, _ in COLUMNS:
            column = self._table.columns.get(ColumnKey(key))
            if column is not None:
                column.label = Text(header + (arrow if key == active else ""))
        self._table.refresh()

    def _cells(self, row: SymbolRow) -> tuple[Text, ...]:
        symbol = Text(row.symbol, style="bold")
        if row.degraded:
            symbol.append("~", style="yellow")  # thin history, treat with care
        if row.stale:
            symbol.stylize("grey50")
        return (
            symbol,
            _num(row.ltp, "{:,.1f}"),
            _change(row.change_pct),
            _num(row.volume, "{:,.0f}", style="grey62"),
            _rvol(row.rvol, row.flashing),
            _num(row.rvol_day, "{:.2f}x", style="grey62"),
        )

    def _status(self, snapshot: Snapshot, shown: int) -> Text:
        if not snapshot.rows:
            empty = Text()
            empty.append("no watchlist loaded  ", style="grey62")
            empty.append("press 'n' to create one", style="yellow")
            if self._event_log:
                empty.append("   " + " | ".join(self._event_log[-2:]), style="yellow")
            return empty

        now = self.services.calendar.now()
        status = Text()
        status.append(f"{now:%H:%M:%S}  ", style="grey62")
        if snapshot.session_date is not None and snapshot.session_date != now.date():
            # Weekend, holiday or pre-open: these are the previous session's numbers.
            status.append(f"as of {snapshot.session_date:%a %d %b} close  ", style="yellow")
        elif snapshot.minute is not None:
            status.append(f"min {snapshot.minute + 1}/{snapshot.session_minutes}  ", style="grey62")
        else:
            status.append("outside session  ", style="grey62")
        arrow = "v" if self._descending else "^"
        status.append(f"sort {SORTABLE[self._sort_index]}{arrow}  ", style="cyan")
        if self._filter:
            status.append(f"/{self._filter}  ", style="yellow")
        status.append(f"{shown}/{len(snapshot.rows)} rows  ", style="grey62")
        if self._frozen:
            status.append("FROZEN  ", style="yellow bold")
        elif time.monotonic() < self._nav_until:
            status.append("holding  ", style="grey62")
        status.append(f"{snapshot.ticks_received:,} ticks", style="grey62")
        if self._event_log:
            status.append("   " + " | ".join(self._event_log[-2:]), style="yellow")
        return status


def _num(value: float | None, fmt: str, style: str = "") -> Text:
    return Text("-", style="grey50") if value is None else Text(fmt.format(value), style=style)


def _change(value: float | None) -> Text:
    if value is None:
        return Text("-", style="grey50")
    return Text(f"{value:+.2f}", style="green" if value >= 0 else "red")


def _rvol(value: float | None, flashing: bool) -> Text:
    if value is None:
        return Text("-", style="grey50")
    if flashing:
        style = "black on green"  # just crossed the alert level
    elif value >= STRONG_RATIO:
        style = "green bold"
    elif value >= MODERATE_RATIO:
        style = "yellow"
    else:
        style = ""  # quiet volume isn't bearish, so no red
    return Text(f"{value:.2f}x", style=style)
