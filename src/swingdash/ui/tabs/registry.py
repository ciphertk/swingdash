"""
The dashboard's tabs, in display order. Adding a tab = one package under
ui/tabs/ plus one entry here.

Factories import their module lazily, so a tab's dependencies load only when
it's first opened. Tab keys must avoid the app's reserved keys:
1-9, w, n, e, d, q and ctrl+p.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from swingdash.ui.tabs.base import TabBase


@dataclass(frozen=True)
class TabSpec:
    id: str
    title: str
    factory: Callable[[], TabBase]
    # Stylesheet relative to the ui package, or None.
    css_path: str | None = None


def _live_rvol() -> TabBase:
    from swingdash.ui.tabs.rvol.pane import LiveRvolTab

    return LiveRvolTab()


def _securities() -> TabBase:
    from swingdash.ui.tabs.securities.pane import SecuritiesTab

    return SecuritiesTab()


def _scanner() -> TabBase:
    from swingdash.ui.tabs.scanner.pane import ScannerTab

    return ScannerTab()


TABS: tuple[TabSpec, ...] = (
    TabSpec(id="live-rvol", title="Live RVOL", factory=_live_rvol, css_path="tabs/rvol/rvol.tcss"),
    TabSpec(
        id="securities",
        title="Securities",
        factory=_securities,
        css_path="tabs/securities/securities.tcss",
    ),
    TabSpec(id="scanner", title="Scanner", factory=_scanner, css_path="tabs/scanner/scanner.tcss"),
)
