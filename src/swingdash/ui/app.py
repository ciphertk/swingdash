"""
SwingDashApp - the dashboard shell.

Owns what is genuinely app-wide: the header (active watchlist, market state),
the tab strip, and the active watchlist itself. Everything else lives in a
tab. Only watchlist SYMBOLS are shared; tabs never read each other's state -
anything two tabs need comes from a shared service instead.
"""

from __future__ import annotations

import logging
from functools import partial

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.reactive import var
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, TabbedContent, TabPane

from swingdash.domain.watchlist import Watchlist, parse_symbols_text
from swingdash.services.container import Services
from swingdash.services.watchlists import WatchlistExistsError
from swingdash.ui.commands import DashboardCommands
from swingdash.ui.tabs.base import TabBase
from swingdash.ui.tabs.registry import TABS, TabSpec
from swingdash.ui.watchlist.bar import WatchlistBar
from swingdash.ui.watchlist.confirm_modal import ConfirmModal
from swingdash.ui.watchlist.edit_modal import WatchlistModal
from swingdash.ui.widgets.error_panel import ErrorPanel
from swingdash.ui.widgets.market_badge import MarketBadge

logger = logging.getLogger(__name__)

# App-wide actions that must not fire while a dialog is open on top.
_BLOCKED_UNDER_MODAL = {
    "focus_watchlist",
    "new_watchlist",
    "edit_watchlist",
    "delete_watchlist",
    "switch_tab",
    "switch_tab_number",
}

# Digits pick a tab by position, resolved against the tabs this app instance
# was built with (not the module registry), so injected tab sets work too.
_TAB_NUMBER_BINDINGS: list[BindingType] = [
    Binding(str(number), f"switch_tab_number({number})", f"Tab {number}", show=False)
    for number in range(1, 10)
]


class SwingDashApp(App[None]):
    TITLE = "swingdash"
    CSS_PATH = ["app.tcss", *(spec.css_path for spec in TABS if spec.css_path)]
    COMMANDS = App.COMMANDS | {DashboardCommands}
    AUTO_FOCUS = None  # tabs decide what gets focus

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("w", "focus_watchlist", "Watchlist"),
        Binding("n", "new_watchlist", "New list"),
        Binding("e", "edit_watchlist", "Edit list"),
        Binding("d", "delete_watchlist", "Delete list"),
        *_TAB_NUMBER_BINDINGS,
    ]

    # The active global watchlist. Tabs that use one watch this attribute.
    watchlist: var[Watchlist | None] = var(None)

    def __init__(
        self,
        services: Services,
        initial_watchlist: Watchlist | None = None,
        tabs: tuple[TabSpec, ...] = TABS,
    ) -> None:
        super().__init__()
        self.services = services
        self.tabs = tabs
        self._initial_watchlist = initial_watchlist
        self._mounted_tabs: dict[str, TabBase | None] = {}
        self._active_tab: str | None = None
        self._shell_ready = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="topbar"):
            yield WatchlistBar(id="watchlist-bar")
            yield MarketBadge(self.services, id="market-badge")
        with TabbedContent(id="tabs"):
            for spec in self.tabs:
                yield TabPane(spec.title, id=spec.id)
        yield Footer()

    def on_mount(self) -> None:
        self._tabbed = self.query_one("#tabs", TabbedContent)
        self._bar = self.query_one("#watchlist-bar", WatchlistBar)
        self.watchlist = self._initial_watchlist
        self._shell_ready = True
        self._activate(self._tabbed.active)

    # --- tabs ----------------------------------------------------------------

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if self._shell_ready and event.pane.id is not None:
            self._activate(event.pane.id)

    def action_switch_tab(self, tab_id: str) -> None:
        self._tabbed.active = tab_id

    def action_switch_tab_number(self, number: int) -> None:
        if 1 <= number <= len(self.tabs):
            self.action_switch_tab(self.tabs[number - 1].id)

    def _activate(self, tab_id: str) -> None:
        if not tab_id or tab_id == self._active_tab:
            return
        previous = self._mounted_tabs.get(self._active_tab or "")
        if previous is not None:
            previous.deactivate()
        self._active_tab = tab_id

        if tab_id not in self._mounted_tabs:
            self._mounted_tabs[tab_id] = self._mount_tab(tab_id)
        tab = self._mounted_tabs[tab_id]
        if tab is not None:
            tab.activate()

    def _mount_tab(self, tab_id: str) -> TabBase | None:
        """Build a tab the first time it's opened. A broken tab gets an error
        panel instead of taking the whole dashboard down."""
        spec = next(s for s in self.tabs if s.id == tab_id)
        pane = self._tabbed.get_pane(tab_id)
        try:
            tab = spec.factory()
        except Exception as exc:
            logger.exception("could not build tab %s", tab_id)
            pane.mount(ErrorPanel(spec.title, exc))
            return None
        pane.mount(tab)
        return tab

    # --- global watchlist ---------------------------------------------------

    def select_watchlist(self, name: str | None) -> None:
        """Make a saved watchlist active everywhere, and remember it."""
        watchlist = self.services.watchlists.get(name) if name else None
        self.services.watchlists.set_active(watchlist.name if watchlist else None)
        self.watchlist = watchlist

    def show_watchlist(self, name: str) -> None:
        """A tab saved a watchlist: list it in the picker and make it active."""
        self._bar.reload()
        self.select_watchlist(name)

    def action_focus_watchlist(self) -> None:
        self._bar.focus_picker()

    def action_new_watchlist(self) -> None:
        self.push_screen(
            WatchlistModal("New watchlist", taken_names=self._saved_names()),
            partial(self._save_watchlist, None),
        )

    def action_edit_watchlist(self) -> None:
        current = self.watchlist
        if current is None or self.services.watchlists.get(current.name) is None:
            self.action_new_watchlist()  # nothing saved selected - editing means creating
            return
        self.push_screen(
            WatchlistModal(
                f"Edit '{current.name}'",
                current.name,
                "\n".join(current.symbols),
                taken_names=self._saved_names() - {current.name},
            ),
            # Remember which list is being edited, so a changed name renames
            # it instead of saving a second copy beside the original.
            partial(self._save_watchlist, current.name),
        )

    def action_delete_watchlist(self) -> None:
        current = self.watchlist
        if current is None or self.services.watchlists.get(current.name) is None:
            self.notify("No saved watchlist selected.", severity="warning")
            return
        self.push_screen(
            ConfirmModal(
                f"Delete watchlist '{current.name}' ({len(current.symbols)} symbols)?\n"
                "This cannot be undone."
            ),
            self._confirm_delete,
        )

    def _saved_names(self) -> set[str]:
        return {w.name for w in self.services.watchlists.all()}

    def _save_watchlist(self, editing: str | None, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        name, text = result
        try:
            self.services.watchlists.save(name, parse_symbols_text(text), replacing=editing)
        except WatchlistExistsError as exc:
            # The dialog already refuses taken names; this only guards a race.
            self.notify(str(exc), severity="error")
            return
        if editing is not None and editing != name:
            self.notify(f"Renamed '{editing}' to '{name}'.")
        self._bar.reload()
        self.select_watchlist(name)

    def _confirm_delete(self, confirmed: bool | None) -> None:
        current = self.watchlist
        if not confirmed or current is None:
            return
        self.services.watchlists.delete(current.name)
        self.notify(f"Deleted '{current.name}'.")
        # Fall through to whatever is left rather than pointing at a list
        # that no longer exists.
        remaining = self.services.watchlists.all()
        self._bar.reload()
        self.select_watchlist(remaining[0].name if remaining else None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Text boxes already swallow printable keys, so shortcuts can't fire
        # while typing; this only stops app actions stacking dialogs on
        # top of an open dialog.
        return not (action in _BLOCKED_UNDER_MODAL and isinstance(self.screen, ModalScreen))
