"""
Parsers for Chartink's responses and pages. Pure: text/JSON in, domain
objects out, tested against trimmed real captures in tests/fixtures/chartink.

Shapes (verified Sep 2026):
- /screener/process: {"data": [{"sr", "nsecode", "name", "bsecode", "close",
  "per_chg", "volume", ...custom columns}], ...}
- /widget/process: {"metaData": [{"columnAliases", "groups", "lastUpdateTime",
  "availableLimit", ...}], "groupData": [{"name", "results": [{alias: [values]}]}]}
  - each alias holds one value per bar; the last one is the latest.
- dashboard page: `:dashboard` and `:widgets` JSON attributes on the Vue root.
  `:template-widgets` is Chartink's starter set on every page, not the dashboard's.
- screener page: `:scan-json` with the ready clause in `atlas_query`.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from typing import Any

from swingdash.adapters.chartink.http import ChartinkFormatError
from swingdash.domain.calendar import IST
from swingdash.domain.chartink import (
    ChartinkResult,
    ChartinkRow,
    DashboardDef,
    ScreenerDef,
    Value,
    WidgetDef,
)

# Screener fields that are bookkeeping, not data worth a column.
_SCREENER_HIDDEN = {"sr"}


def parse_screener_response(payload: Any) -> ChartinkResult:
    try:
        data: list[dict[str, Any]] = payload["data"]
    except (KeyError, TypeError) as exc:
        raise ChartinkFormatError("unexpected screener response shape") from exc
    if not isinstance(data, list):
        raise ChartinkFormatError("screener response 'data' is not a list")

    columns: list[str] = []
    rows: list[ChartinkRow] = []
    for record in data:
        for column in record:
            if column not in _SCREENER_HIDDEN and column not in columns:
                columns.append(column)
        key = str(record.get("nsecode") or record.get("bsecode") or record.get("name") or "")
        rows.append(
            ChartinkRow(key, {c: _value(v) for c, v in record.items() if c not in _SCREENER_HIDDEN})
        )
    return ChartinkResult(tuple(columns), tuple(rows), group_by="symbol")


def parse_widget_response(payload: Any) -> ChartinkResult:
    try:
        meta: dict[str, Any] = payload["metaData"][0]
        aliases: list[str] = list(meta["columnAliases"])
        groups: list[str] = list(meta.get("groups") or [])
        group_data: list[dict[str, Any]] = payload["groupData"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChartinkFormatError("unexpected widget response shape") from exc

    rows: list[ChartinkRow] = []
    for group in group_data:
        values: dict[str, Value] = {}
        for result in group.get("results") or []:
            for alias, series in result.items():
                latest = series[-1] if isinstance(series, list) and series else series
                values[alias] = None if isinstance(latest, list) else _value(latest)
        rows.append(ChartinkRow(str(group.get("name", "")), values))

    updated = meta.get("lastUpdateTime")
    return ChartinkResult(
        columns=tuple(aliases),
        rows=tuple(rows),
        group_by=groups[0] if groups else None,
        data_time=(
            dt.datetime.fromtimestamp(updated / 1000, IST)
            if isinstance(updated, int | float)
            else None
        ),
        available=meta.get("availableLimit")
        if isinstance(meta.get("availableLimit"), int)
        else None,
    )


def parse_dashboard_page(page: str) -> DashboardDef:
    dashboard = _json_attribute(page, ":dashboard")
    if not isinstance(dashboard, dict):
        raise ChartinkFormatError(
            "No dashboard found on that page - it may be private (Chartink only shows "
            "private dashboards to their owner) or the link may be wrong"
        )
    widgets = _json_attribute(page, ":widgets")
    return DashboardDef(
        id=int(dashboard.get("id", 0)),
        name=str(dashboard.get("name") or "Dashboard"),
        is_private=bool(dashboard.get("is_private")),
        widgets=tuple(_widget(w) for w in widgets if isinstance(w, dict) and w.get("query"))
        if isinstance(widgets, list)
        else (),
    )


def parse_screener_page(page: str) -> ScreenerDef:
    scan = _json_attribute(page, ":scan-json")
    if not isinstance(scan, dict):
        raise ChartinkFormatError(
            "No screener found on that page - it may be private or the link may be wrong"
        )
    clause = scan.get("atlas_query")
    return ScreenerDef(
        id=scan.get("id") if isinstance(scan.get("id"), int) else None,
        name=str(scan.get("name") or scan.get("slug") or "Screener"),
        slug=str(scan.get("slug") or ""),
        is_private=bool(scan.get("is_private")),
        clause=str(clause).strip() if clause else None,
    )


def _widget(raw: dict[str, Any]) -> WidgetDef:
    details = raw.get("jsondetails")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = None
    result_type = details.get("resultType") if isinstance(details, dict) else None
    return WidgetDef(
        id=int(raw.get("id", 0)),
        name=str(raw.get("name") or f"Widget {raw.get('id')}"),
        query=str(raw["query"]),
        result_type=str(result_type) if result_type else None,
    )


def _json_attribute(page: str, name: str) -> Any:
    match = re.search(r"\s" + re.escape(name) + r'="([^"]*)"', page)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except ValueError as exc:
        raise ChartinkFormatError(f"page attribute {name} is not valid JSON") from exc


def _value(value: Any) -> Value:
    if value is None or isinstance(value, bool):
        return None if value is None else str(value)
    if isinstance(value, int | float):
        return value
    return str(value)
