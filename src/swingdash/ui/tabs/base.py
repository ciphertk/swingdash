"""
Base class for dashboard tabs.

A tab is mounted the first time it's opened, then kept. While hidden its
rendering is paused, but its services keep running (live data keeps flowing,
alerts keep firing, nothing is re-fetched when you come back).

Subclasses implement `refresh_view()` and, if they use the global watchlist,
set `uses_watchlist = True` and implement `on_watchlist_changed()`. They must
not define `on_mount` themselves - override `on_tab_mount()` instead, so the
base setup always runs exactly once.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import Static

from swingdash.domain.watchlist import Watchlist

if TYPE_CHECKING:
    from swingdash.services.container import Services

logger = logging.getLogger(__name__)


class _Dashboard(Protocol):
    services: Services
    watchlist: Watchlist | None


class TabBase(Vertical):
    uses_watchlist: ClassVar[bool] = False
    REFRESH_HZ: ClassVar[float] = 2.0

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._timer: Timer | None = None
        self._active = False
        self._failed = False

    # --- for subclasses ----------------------------------------------------

    def on_tab_mount(self) -> None:
        """One-time setup after children are mounted."""

    def refresh_view(self) -> None:
        """Called at REFRESH_HZ while the tab is visible."""

    def on_watchlist_changed(self, watchlist: Watchlist | None) -> None:
        """Called with the active global watchlist (and on every change) when uses_watchlist."""

    @property
    def services(self) -> Services:
        return cast(_Dashboard, self.app).services

    @property
    def active_watchlist(self) -> Watchlist | None:
        return cast(_Dashboard, self.app).watchlist

    # --- lifecycle (driven by the app) -------------------------------------

    def on_mount(self) -> None:
        try:
            self.on_tab_mount()
            if self.uses_watchlist:
                self.watch(self.app, "watchlist", self._watchlist_changed, init=True)
        except Exception as exc:
            self._fail(exc)
            return
        self._timer = self.set_interval(1 / self.REFRESH_HZ, self._safe_refresh)
        if not self._active:
            self._timer.pause()

    def activate(self) -> None:
        self._active = True
        if self._timer is not None and not self._failed:
            self._timer.resume()
            self._safe_refresh()

    def deactivate(self) -> None:
        self._active = False
        if self._timer is not None:
            self._timer.pause()

    def _watchlist_changed(self, watchlist: Watchlist | None) -> None:
        if self._failed:
            return
        try:
            self.on_watchlist_changed(watchlist)
        except Exception as exc:
            self._fail(exc)

    def _safe_refresh(self) -> None:
        if self._failed:
            return
        try:
            self.refresh_view()
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        """Contain the failure to this tab: log it, say so, stop refreshing."""
        self._failed = True
        logger.error("tab %s failed", type(self).__name__, exc_info=exc)
        if self._timer is not None:
            self._timer.stop()
        self.mount(
            Static(
                f"This tab stopped after an error: {type(exc).__name__}: {exc}\n"
                "Other tabs are unaffected. Details are in the log file.",
                classes="tab-error",
            ),
            before=0,
        )
