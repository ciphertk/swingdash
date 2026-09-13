"""Command palette (ctrl+p) entries: watchlist management and tab navigation."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from swingdash.ui.app import SwingDashApp


class DashboardCommands(Provider):
    def _commands(self) -> list[tuple[str, Callable[[], object], str]]:
        app = cast("SwingDashApp", self.app)
        commands: list[tuple[str, Callable[[], object], str]] = [
            ("New watchlist", app.action_new_watchlist, "Create a watchlist by pasting symbols"),
            ("Edit watchlist", app.action_edit_watchlist, "Edit the active watchlist"),
            ("Delete watchlist", app.action_delete_watchlist, "Delete the active watchlist"),
        ]
        commands += [
            (
                f"Watchlist: {w.name}",
                partial(app.select_watchlist, w.name),
                f"Switch to {w.name} ({len(w.symbols)} symbols)",
            )
            for w in app.services.watchlists.all()
        ]
        commands += [
            (f"Tab: {spec.title}", partial(app.action_switch_tab, spec.id), f"Open {spec.title}")
            for spec in app.tabs
        ]
        return commands

    async def discover(self) -> Hits:
        for text, callback, help_text in self._commands():
            yield DiscoveryHit(text, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for text, callback, help_text in self._commands():
            score = matcher.match(text)
            if score > 0:
                yield Hit(score, matcher.highlight(text), callback, help=help_text)
