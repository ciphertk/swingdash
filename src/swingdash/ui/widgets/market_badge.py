"""
Market state, top right: open/closed plus an NSE holiday chip when today has
a holiday entry.

The chip reports whether NSE ITSELF is shut - a SETTLEMENT_HOLIDAY or
SPECIAL_TIMING day still trades normally, so calling those "closed" would be
wrong.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

from swingdash.services.container import Services

# The feed reports exchange status live, but only once something has
# subscribed. Until then status is derived from the calendar - at most once
# a minute, since that lookup may hit the network on a non-trading day.
_DERIVE_EVERY_SECONDS = 60


class MarketBadge(Static):
    def __init__(self, services: Services, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._services = services
        self._derived_status = "UNKNOWN"
        self._derived_at = float("-inf")

    def on_mount(self) -> None:
        self.refresh_badge()
        self.set_interval(1.0, self.refresh_badge)

    def refresh_badge(self) -> None:
        self.update(self.render_badge(self._market_status()))

    def _market_status(self) -> str:
        status = self._services.hub.market_status
        if status != "UNKNOWN":
            return status
        if time.monotonic() - self._derived_at > _DERIVE_EVERY_SECONDS:
            self._derived_at = time.monotonic()
            calendar = self._services.calendar
            now = calendar.now()
            session = calendar.get_session(now.date())
            is_open = session is not None and session.open_at <= now < session.close_at
            self._derived_status = "NORMAL_OPEN" if is_open else "CLOSED"
        return self._derived_status

    def render_badge(self, market_status: str) -> Text:
        badge = Text()
        holiday = self._services.calendar.holiday_for()
        if holiday is not None:
            style = "black on red" if holiday.nse_closed else "black on yellow"
            badge.append(f" {holiday.label.upper()} ", style=style)
            badge.append(f" {holiday.description}  ", style="grey62")

        if market_status == "NORMAL_OPEN":
            badge.append("* OPEN", style="green bold")
        else:
            label = (
                market_status.replace("_", " ").title()
                if market_status not in {"UNKNOWN", "CLOSED"}
                else "Closed"
            )
            badge.append(f"o {label}", style="grey50")
        return badge
