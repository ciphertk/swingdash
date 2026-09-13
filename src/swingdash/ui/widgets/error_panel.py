"""Shown in place of a tab whose construction failed, so the rest of the app keeps working."""

from __future__ import annotations

from textual.widgets import Static


class ErrorPanel(Static):
    def __init__(self, title: str, error: BaseException) -> None:
        super().__init__(
            f"The '{title}' tab couldn't be opened: {type(error).__name__}: {error}\n"
            "Other tabs are unaffected. Details are in the log file.",
            classes="tab-error",
        )
