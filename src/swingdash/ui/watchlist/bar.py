"""
The global watchlist picker. Presentational only: it mirrors the app's
`watchlist` and asks the app to switch; the app owns persistence and CRUD.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label, Select

from swingdash.domain.watchlist import Watchlist

if TYPE_CHECKING:
    from swingdash.ui.app import SwingDashApp


class WatchlistBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Label("Watchlist", classes="bar-label")
        yield Select[str]([], prompt="no watchlist", id="watchlist-select")

    @property
    def dashboard(self) -> SwingDashApp:
        return cast("SwingDashApp", self.app)

    def on_mount(self) -> None:
        self._select = self.query_one("#watchlist-select", Select)
        self.reload()
        self.watch(self.app, "watchlist", self._sync, init=True)

    def focus_picker(self) -> None:
        self._select.focus()

    def reload(self) -> None:
        saved = self.dashboard.services.watchlists.all()
        self._select.set_options([(f"{w.name}  ({len(w.symbols)})", w.name) for w in saved])
        self._sync(self.dashboard.watchlist)

    def _sync(self, watchlist: Watchlist | None) -> None:
        names = {w.name for w in self.dashboard.services.watchlists.all()}
        with self._select.prevent(Select.Changed):
            if watchlist is not None and watchlist.name in names:
                self._select.value = watchlist.name
            else:
                self._select.clear()

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        # Guard on type: a blank selection arrives as a NoSelection instance,
        # while Select.BLANK is the bool False in this Textual version.
        if isinstance(event.value, str):
            self.dashboard.select_watchlist(event.value)
