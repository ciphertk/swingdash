"""
Live RVOL terminal.

Built on DataTable rather than a rendered-to-Static Rich table: that's
what provides arrow-key navigation, scrolling, a row cursor and sticky
headers. Cells are updated in place by row key, so the cursor and scroll
position survive every refresh.

Re-ranking is deliberately NOT done every frame. Values update at
REFRESH_HZ, but row ORDER only settles every REORDER_SECONDS - and pauses
entirely while you're navigating - so rows never slide out from under the
cursor while you're reading them.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import Footer, Header, Input, Select, Static
from textual.widgets.data_table import ColumnKey

from swingdash.live.engine import RvolEngine
from swingdash.live.rvol_calc import MODERATE_RATIO, STRONG_RATIO
from swingdash.live.session import holiday_for, now_ist
from swingdash.live.types import Snapshot, SymbolRow
from swingdash.services import watchlist_service
from swingdash.tui.confirm_modal import ConfirmModal
from swingdash.tui.rvol_table import RvolTable
from swingdash.tui.watchlist_modal import WatchlistModal

REFRESH_HZ = 8
REORDER_SECONDS = 2.0
# After a keypress, hold the current ordering so a row being read doesn't
# jump away mid-glance.
NAV_FREEZE_SECONDS = 5.0

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


class RvolApp(App):
    AUTO_FOCUS = None  # the table is focused explicitly, never the filter box

    CSS = """
    Screen { background: $surface; }
    #toolbar { height: 3; padding: 0 1; }
    #watchlist { width: 34; }
    #market { width: 1fr; content-align: right middle; padding: 1 0 0 0; }
    DataTable { height: 1fr; }
    DataTable > .datatable--cursor { background: $accent 40%; }
    #status { height: auto; padding: 0 1; }
    Input { border: tall $accent; }
    Input.hidden { display: none; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "cycle_sort", "Sort"),
        ("r", "reverse_sort", "Reverse"),
        ("slash", "focus_filter", "Filter"),
        ("escape", "clear_filter", "Clear"),
        ("f", "toggle_freeze", "Freeze"),
        ("w", "focus_watchlist", "Watchlist"),
        ("n", "new_watchlist", "New"),
        ("e", "edit_watchlist", "Edit"),
        ("d", "delete_watchlist", "Delete"),
    ]

    def __init__(self, engine: RvolEngine, watchlist_name: str | None = None) -> None:
        super().__init__()
        self.engine = engine
        self._watchlist_name = watchlist_name
        self._sort_index = 0
        self._descending = True
        self._filter = ""
        self._event_log: list[str] = []
        self._rendered: dict[str, tuple] = {}  # row key -> last cell contents
        self._order: list[str] = []  # row keys, in displayed order
        self._last_reorder = 0.0
        self._nav_until = 0.0
        self._frozen = False
        # Until you actually move the cursor, it stays pinned to the top so
        # the hottest row is always under it. Once you navigate, it follows
        # that SYMBOL through re-sorts instead.
        self._cursor_follows_symbol = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Horizontal(id="toolbar"):
                options = _watchlist_options()
                initial = next(
                    (value for _, value in options if value == self._watchlist_name), Select.NULL
                )
                yield Select(options, prompt="watchlist", id="watchlist", value=initial)
                yield Static(id="market")
            yield RvolTable(id="table", cursor_type="row", zebra_stripes=True)
            yield Input(placeholder="filter symbols...", id="filter", classes="hidden")
            yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Live RVOL"
        # Cached rather than looked up per frame: query_one searches the
        # ACTIVE screen, so once a modal is pushed it would stop finding
        # these. Caching also avoids a DOM query 8x a second.
        self._table = self.query_one("#table", RvolTable)
        self._status_line = self.query_one("#status", Static)
        self._market = self.query_one("#market", Static)
        self._filter_input = self.query_one("#filter", Input)
        self._select = self.query_one("#watchlist", Select)

        for _, key, header, width in COLUMNS:
            self._table.add_column(header, key=key, width=width)
        self._table.focus()  # so arrow keys / page / home / end work immediately

        self._reload_watchlists()
        self.engine.start()
        self.set_interval(1 / REFRESH_HZ, self._tick)

    # --- watchlists --------------------------------------------------------

    def _reload_watchlists(self) -> None:
        select = self._select
        options = _watchlist_options()
        select.set_options(options)
        if any(value == self._watchlist_name for _, value in options):
            # set_options clears the value; restore it without re-triggering
            # a switch we've already applied.
            with select.prevent(Select.Changed):
                select.value = self._watchlist_name

    def action_focus_watchlist(self) -> None:
        self._select.focus()

    def action_new_watchlist(self) -> None:
        self.push_screen(WatchlistModal("New watchlist"), self._save_watchlist)

    def action_edit_watchlist(self) -> None:
        name = self._watchlist_name
        if not name:
            self.action_new_watchlist()  # nothing selected - editing means creating
            return
        symbols = watchlist_service.get_watchlist(name) or []
        self.push_screen(
            WatchlistModal(f"Edit '{name}'", name, "\n".join(symbols)),
            self._save_watchlist,
        )

    def action_delete_watchlist(self) -> None:
        name = self._watchlist_name
        if not name:
            self._event_log.append("no saved watchlist selected")
            return
        count = len(watchlist_service.get_watchlist(name) or [])
        self.push_screen(
            ConfirmModal(f"Delete watchlist '{name}' ({count} symbols)?\nThis cannot be undone."),
            self._confirm_delete,
        )

    def _confirm_delete(self, confirmed: bool | None) -> None:
        self._table.focus()
        if not confirmed or not self._watchlist_name:
            return

        deleted = self._watchlist_name
        watchlist_service.delete_watchlist(deleted)
        self._event_log.append(f"deleted '{deleted}'")

        # Fall through to whatever is left rather than leaving the table
        # pointing at a watchlist that no longer exists.
        remaining = watchlist_service.list_watchlists()
        self._watchlist_name = remaining[0]["name"] if remaining else None
        self._reload_watchlists()
        self._switch_to(remaining[0]["symbols"] if remaining else [])

    def _save_watchlist(self, result: tuple[str, str] | None) -> None:
        if result is None:
            self._table.focus()
            return
        name, text = result
        symbols = watchlist_service.parse_symbols_text(text)
        watchlist_service.save_watchlist(name, symbols)
        self._watchlist_name = name
        self._reload_watchlists()
        self._switch_to(symbols)

    def on_select_changed(self, event: Select.Changed) -> None:
        # Guard on the type we actually store rather than comparing against
        # Select.BLANK: in this Textual version BLANK is the bool False
        # while a real blank selection arrives as a NoSelection instance,
        # so an identity check silently lets the sentinel through.
        if not isinstance(event.value, str) or event.value == self._watchlist_name:
            return
        symbols = watchlist_service.get_watchlist(event.value)
        if symbols is None:
            return
        self._watchlist_name = event.value
        self._switch_to(symbols)

    def _switch_to(self, symbols: list[str]) -> None:
        self.engine.set_symbols(symbols)
        # Row identities all changed, so drop the cached render and let the
        # next tick rebuild from scratch.
        self._rendered.clear()
        self._order = []
        self._cursor_follows_symbol = False
        self._table.focus()

    def on_unmount(self) -> None:
        self.engine.stop()

    # --- actions -----------------------------------------------------------

    def check_action(self, action: str, parameters) -> bool:
        """
        While the filter box has focus, letters must reach it as text
        rather than firing shortcuts. Escape stays live so there's always
        a way back out.
        """
        if action == "clear_filter" or not isinstance(self.focused, Input):
            return True
        return action not in {
            "cycle_sort",
            "reverse_sort",
            "focus_filter",
            "quit",
            "toggle_freeze",
            "focus_watchlist",
            "new_watchlist",
            "edit_watchlist",
            "delete_watchlist",
        }

    def action_cycle_sort(self) -> None:
        self._sort_index = (self._sort_index + 1) % len(SORTABLE)
        self._reorder(force=True)

    def action_reverse_sort(self) -> None:
        self._descending = not self._descending
        self._reorder(force=True)

    def action_toggle_freeze(self) -> None:
        self._frozen = not self._frozen

    def action_focus_filter(self) -> None:
        field = self._filter_input
        field.remove_class("hidden")
        field.focus()

    def action_clear_filter(self) -> None:
        field = self._filter_input
        field.value = ""
        self._filter = ""
        field.add_class("hidden")
        self._table.focus()

    # Input messages bubble up from any Input in the app - including the
    # ones inside the watchlist modal - so both handlers must check which
    # box actually sent them. Without this, typing a watchlist name set the
    # table filter to that name and blanked every row.
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        self._filter = event.value.strip().upper()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter":
            return
        # Enter applies the filter and hands focus back so arrows work again.
        self._filter_input.add_class("hidden")
        self._table.focus()

    def on_key(self, event) -> None:
        if event.key in ("up", "down", "pageup", "pagedown", "home", "end", "g", "G"):
            self._nav_until = time.monotonic() + NAV_FREEZE_SECONDS
            self._cursor_follows_symbol = True

    # --- render loop -------------------------------------------------------

    def _tick(self) -> None:
        for event in self.engine.events():
            self._event_log.append(event)
        del self._event_log[:-3]

        snapshot = self.engine.snapshot()
        rows = {
            r.instrument_key: r
            for r in snapshot.rows
            if not self._filter or self._filter in r.symbol
        }

        if set(rows) != set(self._order):
            self._rebuild(rows)  # filter changed, or symbols resolved
        else:
            self._update_cells(rows)
            self._maybe_reorder(rows)

        self._status_line.update(self._status(snapshot, len(rows)))
        self._market.update(self._market_badge(snapshot))

    def _rebuild(self, rows: dict[str, SymbolRow]) -> None:
        """
        Re-add every row in sort order. Rows are keyed, so capturing the
        cursor's key beforehand and looking it up afterwards keeps the
        selection on the same SYMBOL rather than the same screen position.
        """
        table = self._table
        cursor_key = self._cursor_key()

        table.clear()
        self._rendered.clear()
        self._order = self._sorted_keys(rows)
        for key in self._order:
            cells = self._cells(rows[key])
            table.add_row(*cells, key=key)
            self._rendered[key] = cells

        self._restore_cursor(cursor_key)
        self._update_headers()
        self._last_reorder = time.monotonic()

    def _update_cells(self, rows: dict[str, SymbolRow]) -> None:
        table = self._table
        for key, row in rows.items():
            cells = self._cells(row)
            if self._rendered.get(key) == cells:
                continue  # nothing changed - skip six no-op writes
            for (_, column_key, _, _), value in zip(COLUMNS, cells, strict=True):
                table.update_cell(key, column_key, value)
            self._rendered[key] = cells

    def _maybe_reorder(self, rows: dict[str, SymbolRow]) -> None:
        now = time.monotonic()
        if self._frozen or now < self._nav_until:
            return
        if now - self._last_reorder < REORDER_SECONDS:
            return
        desired = self._sorted_keys(rows)
        if desired != self._order:
            self._reorder(force=True, rows=rows)
        self._last_reorder = now

    def _reorder(self, force: bool = False, rows: dict[str, SymbolRow] | None = None) -> None:
        """
        Ordering is applied by re-adding rows, not DataTable.sort - sort
        compares cell values, and ours are styled Text objects, which
        don't compare numerically.
        """
        if rows is None:
            snapshot = self.engine.snapshot()
            rows = {
                r.instrument_key: r
                for r in snapshot.rows
                if not self._filter or self._filter in r.symbol
            }
        self._rebuild(rows)

    # --- helpers -----------------------------------------------------------

    def _sorted_keys(self, rows: dict[str, SymbolRow]) -> list[str]:
        attr = SORTABLE[self._sort_index]

        # Rows without a value (no baseline yet, no tick yet) always sink
        # to the bottom rather than floating to the top when descending.
        present = [k for k in rows if getattr(rows[k], attr) is not None]
        missing = sorted(k for k in rows if getattr(rows[k], attr) is None)
        present.sort(key=lambda k: getattr(rows[k], attr), reverse=self._descending)
        return present + missing

    def _cursor_key(self) -> str | None:
        table = self._table
        if not table.row_count:
            return None
        try:
            return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _restore_cursor(self, cursor_key: str | None) -> None:
        table = self._table
        if not self._cursor_follows_symbol or cursor_key is None or cursor_key not in self._order:
            table.move_cursor(row=0, scroll=True)
            return
        table.move_cursor(row=self._order.index(cursor_key), scroll=True)

    def _update_headers(self) -> None:
        table = self._table
        active = SORTABLE[self._sort_index]
        arrow = " v" if self._descending else " ^"
        for _, key, header, _ in COLUMNS:
            column = table.columns.get(ColumnKey(key))
            if column is not None:
                column.label = Text(header + (arrow if key == active else ""))
        table.refresh()

    def _cells(self, row: SymbolRow) -> tuple:
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

    def _market_badge(self, snapshot: Snapshot) -> Text:
        """
        Top-right state. The holiday chip only appears when today actually
        has an entry, and it reports whether NSE itself is shut - a
        SETTLEMENT_HOLIDAY or SPECIAL_TIMING day still trades normally, so
        calling those "closed" would be wrong.
        """
        badge = Text()

        holiday = holiday_for()
        if holiday is not None:
            if holiday.nse_closed:
                badge.append(f" {holiday.label.upper()} ", style="black on red")
            else:
                badge.append(f" {holiday.label.upper()} ", style="black on yellow")
            badge.append(f" {holiday.description}  ", style="grey62")

        if snapshot.market_status == "NORMAL_OPEN":
            badge.append("* OPEN", style="green bold")
        else:
            label = (
                snapshot.market_status.replace("_", " ").title()
                if snapshot.market_status != "UNKNOWN"
                else "Closed"
            )
            badge.append(f"o {label}", style="grey50")
        return badge

    def _status(self, snapshot: Snapshot, shown: int) -> Text:
        progress = (
            f"min {snapshot.minute + 1}/{snapshot.session_minutes}"
            if snapshot.minute is not None
            else "outside session"
        )
        if not snapshot.rows:
            empty = Text()
            empty.append("no watchlist loaded  ", style="grey62")
            empty.append("press 'n' to create one", style="yellow")
            if self._event_log:
                empty.append("   " + " | ".join(self._event_log[-2:]), style="yellow")
            return empty

        status = Text()
        status.append(f"{now_ist():%H:%M:%S}  ", style="grey62")
        if snapshot.session_date is not None and snapshot.session_date != now_ist().date():
            # Weekend, holiday or pre-open: make it obvious these are the
            # previous session's closing numbers, not today's.
            status.append(f"as of {snapshot.session_date:%a %d %b} close  ", style="yellow")
        else:
            status.append(f"{progress}  ", style="grey62")
        status.append(
            f"sort {SORTABLE[self._sort_index]}{'v' if self._descending else '^'}  ", style="cyan"
        )
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


def _watchlist_options() -> list[tuple[str, str]]:
    return [
        (f"{w['name']}  ({len(w['symbols'])})", w["name"])
        for w in watchlist_service.list_watchlists()
    ]


def _num(value, fmt: str, style: str = "") -> Text:
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
