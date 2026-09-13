"""Small user choices remembered across runs (beyond the active watchlist)."""

from __future__ import annotations

from swingdash.adapters.storage.repos.app_state import AppStateRepository

_SCANNER_INDEX_KEY = "scanner_index"


class PreferencesService:
    def __init__(self, state: AppStateRepository, default_scanner_index: str) -> None:
        self._state = state
        self._default_scanner_index = default_scanner_index

    def scanner_index(self) -> str:
        """The benchmark index Mswing compares against (an instrument key)."""
        return self._state.get(_SCANNER_INDEX_KEY) or self._default_scanner_index

    def set_scanner_index(self, instrument_key: str) -> None:
        self._state.set(_SCANNER_INDEX_KEY, instrument_key)
