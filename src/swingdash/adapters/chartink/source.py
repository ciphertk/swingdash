"""ChartinkSource - runs screeners and widgets, and reads screener/dashboard pages."""

from __future__ import annotations

from swingdash.adapters.chartink import parsers
from swingdash.adapters.chartink.http import TOKEN_PAGE, ChartinkHttp
from swingdash.domain.chartink import (
    ChartinkKind,
    ChartinkRequest,
    ChartinkResult,
    DashboardDef,
    ScreenerDef,
)


class ChartinkSource:
    def __init__(self, http: ChartinkHttp | None = None) -> None:
        self._http = http or ChartinkHttp()

    def run(self, request: ChartinkRequest, referer: str | None = None) -> ChartinkResult:
        if request.kind is ChartinkKind.SCREENER:
            payload = self._http.post("/screener/process", request.fields, referer or TOKEN_PAGE)
            return parsers.parse_screener_response(payload)
        payload = self._http.post("/widget/process", request.fields, referer or TOKEN_PAGE)
        return parsers.parse_widget_response(payload)

    def screener(self, url: str) -> ScreenerDef:
        return parsers.parse_screener_page(self._http.page(url))

    def dashboard(self, url: str) -> DashboardDef:
        return parsers.parse_dashboard_page(self._http.page(url))

    def close(self) -> None:
        self._http.close()
