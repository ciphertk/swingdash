"""
Saved watchlists and the globally active one.

Only watchlist SYMBOLS are app-global. Tabs that need a watchlist read the
active one; tabs that don't (market-wide views) ignore it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.watchlists import WatchlistRepository
from swingdash.domain.watchlist import DEFAULT_WATCHLIST_NAME, Watchlist

_ACTIVE_KEY = "active_watchlist"
_SEEDED_KEY = "watchlists_seeded"


class WatchlistService:
    def __init__(
        self,
        repo: WatchlistRepository,
        state: AppStateRepository,
        default_symbols: Callable[[], list[str]],
    ) -> None:
        self._repo = repo
        self._state = state
        self._default_symbols = default_symbols

    def all(self) -> list[Watchlist]:
        return self._repo.all()

    def get(self, name: str) -> Watchlist | None:
        return self._repo.get(name)

    def save(self, name: str, symbols: Sequence[str]) -> Watchlist:
        watchlist = Watchlist(name=name, symbols=tuple(symbols))
        self._repo.save(watchlist)
        return watchlist

    def delete(self, name: str) -> bool:
        deleted = self._repo.delete(name)
        if deleted and self.active_name() == name:
            self._state.delete(_ACTIVE_KEY)
        return deleted

    def active_name(self) -> str | None:
        return self._state.get(_ACTIVE_KEY)

    def set_active(self, name: str | None) -> None:
        if name is None:
            self._state.delete(_ACTIVE_KEY)
        else:
            self._state.set(_ACTIVE_KEY, name)

    def initial(self, requested: str | None = None) -> Watchlist | None:
        """
        What to open with: the requested list, else the one active last time,
        else "default", else any saved list, else nothing (the user creates
        one from the UI).
        """
        if requested is not None:
            return self.get(requested)
        for name in (self.active_name(), DEFAULT_WATCHLIST_NAME):
            if name and (found := self.get(name)):
                return found
        saved = self.all()
        return saved[0] if saved else None

    def seed_on_first_run(self) -> None:
        """
        Seed the example list exactly once, on a brand-new database. The old
        behaviour re-seeded "default" whenever it was missing, so deleting it
        silently brought it back.
        """
        if self._state.get(_SEEDED_KEY):
            return
        if self._repo.count() == 0:
            self.save(DEFAULT_WATCHLIST_NAME, self._default_symbols())
        self._state.set(_SEEDED_KEY, "1")
