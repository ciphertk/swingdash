"""A ChartinkSourcePort serving captured pages/responses through the real parsers."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from swingdash.adapters.chartink import parsers
from swingdash.domain.chartink import (
    ChartinkKind,
    ChartinkRequest,
    ChartinkResult,
    DashboardDef,
    ScreenerDef,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "chartink"


def _json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeChartinkSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (kind or "page", detail)
        # Force a result (or an exception) for requests whose main field contains a key.
        self.scripted: dict[str, ChartinkResult | Exception] = {}
        self.release = threading.Event()  # set it to let a gated run finish
        self.release.set()
        self.closed = False

    def run(self, request: ChartinkRequest, referer: str | None = None) -> ChartinkResult:
        main = request.fields.get("scan_clause") or request.fields.get("query") or ""
        self.calls.append((request.kind.value, main))
        self.release.wait(5)
        for needle, outcome in self.scripted.items():
            if needle in main:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        if request.kind is ChartinkKind.SCREENER:
            if "column_clause" in request.fields:
                return parsers.parse_screener_response(_json("screener_process_columns.json"))
            return parsers.parse_screener_response(_json("screener_process.json"))
        if "GROUP BY symbol" in main:
            return parsers.parse_widget_response(_json("widget_table.json"))
        if "GROUP BY sector" in main:
            return parsers.parse_widget_response(_json("widget_sector.json"))
        return parsers.parse_widget_response(_json("widget_nogroups.json"))

    def screener(self, url: str) -> ScreenerDef:
        self.calls.append(("page", url))
        # "total-universe-v2" is a screener with custom columns (RVOL%, MSwing).
        page = "screener_page_columns.html" if "total-universe" in url else "screener_page.html"
        return parsers.parse_screener_page((FIXTURES / page).read_text("utf-8"))

    def dashboard(self, url: str) -> DashboardDef:
        self.calls.append(("page", url))
        return parsers.parse_dashboard_page((FIXTURES / "dashboard_page.html").read_text("utf-8"))

    def close(self) -> None:
        self.closed = True
