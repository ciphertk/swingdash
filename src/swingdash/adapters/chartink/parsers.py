"""
Parsers for Chartink's responses and pages. Pure: text/JSON in, domain
objects out, tested against trimmed real captures in tests/fixtures/chartink.

Shapes (verified Sep 2026):
- /screener/process: {"data": [{"sr", "nsecode", "name", "bsecode", "close",
  "per_chg", "volume"}], ...} - or, when the request has a column_clause,
  "scan-column-<id>" values with "<id>-conditional-filters-color" flags.
- /widget/process: {"metaData": [{"columnAliases", "groups", "tradeTimes",
  "lastUpdateTime", "availableLimit", ...}], "groupData": [{"name", "results":
  [{alias: [values]}]}]}
  - each alias holds one value per bar (`tradeTimes`, epoch ms); the last is
    the latest. Grouped widgets keep the latest; an ungrouped one (a single
    "*no-groups*" group) becomes one row per bar, newest first.
  - 1.7e308 (DBL_MAX) stands for "no value" (e.g. a division by zero).
- dashboard page: `:dashboard` and `:widgets` JSON attributes on the Vue root.
  `:template-widgets` is Chartink's starter set on every page, not the dashboard's.
- screener page: `:scan-json` with the ready clause in `atlas_query`, and the
  editor state in `atlas_json` (its `columns.children` name and colour the
  screener's columns).
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
    ColumnSpec,
    DashboardDef,
    ScreenerDef,
    Value,
    WidgetDef,
    color_key,
    column_key,
)

# Screener fields that are bookkeeping, not data worth a column.
_SCREENER_HIDDEN = {"sr"}
# With a column_clause, columns arrive as 'scan-column-<id>' plus a colour
# flag '<id>-conditional-filters-color' (verified Sep 2026).
_SCAN_COLUMN = re.compile(r"^scan-column-(.+)$")
_COLOR_FIELD = re.compile(r"^(.+)-conditional-filters-color$")
# Chartink's "no value": DBL_MAX (1.7976931348623157e308), shown as 1.7e308.
_NO_VALUE = 1e308


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
        values: dict[str, Value] = {}
        for field, raw in record.items():
            if field in _SCREENER_HIDDEN:
                continue
            color = _COLOR_FIELD.match(field)
            if color:
                # A colour flag, not data: kept on the row for its column.
                values[color_key(column_key(color.group(1)))] = _value(raw)
                continue
            scan_column = _SCAN_COLUMN.match(field)
            column = column_key(scan_column.group(1)) if scan_column else field
            values[column] = _value(raw)
            if column not in columns:
                columns.append(column)
        key = str(record.get("nsecode") or record.get("bsecode") or record.get("name") or "")
        rows.append(ChartinkRow(key, values))
    return ChartinkResult(tuple(columns), tuple(rows), group_by="symbol")


def parse_widget_response(payload: Any) -> ChartinkResult:
    if payload == []:
        # Seen for a grouped widget asked for long history: too big to answer.
        raise ChartinkFormatError("Chartink returned no data for this widget")
    try:
        meta: dict[str, Any] = payload["metaData"][0]
        aliases: list[str] = list(meta["columnAliases"])
        groups: list[str] = list(meta.get("groups") or [])
        group_data: list[dict[str, Any]] = payload["groupData"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChartinkFormatError("unexpected widget response shape") from exc

    times = meta.get("tradeTimes")
    if not groups and len(group_data) == 1 and isinstance(times, list) and times:
        rows = _trend_rows(group_data[0], [t for t in times if isinstance(t, int | float)])
        group_by: str | None = "date"
    else:
        rows = []
        for group in group_data:
            values: dict[str, Value] = {}
            for alias, series in _series(group).items():
                values[alias] = _value(series[-1]) if series else None
            rows.append(ChartinkRow(str(group.get("name", "")), values))
        group_by = groups[0] if groups else None

    updated = meta.get("lastUpdateTime")
    return ChartinkResult(
        columns=tuple(aliases),
        rows=tuple(rows),
        group_by=group_by,
        data_time=(
            dt.datetime.fromtimestamp(updated / 1000, IST)
            if isinstance(updated, int | float)
            else None
        ),
        available=meta.get("availableLimit")
        if isinstance(meta.get("availableLimit"), int)
        else None,
    )


def _series(group: dict[str, Any]) -> dict[str, list[Any]]:
    series: dict[str, list[Any]] = {}
    for result in group.get("results") or []:
        for alias, values in result.items():
            series[alias] = values if isinstance(values, list) else [values]
    return series


def _trend_rows(group: dict[str, Any], times: list[float]) -> list[ChartinkRow]:
    """One row per bar, newest first, keyed by its date (and time, for intraday bars)."""
    stamps = [dt.datetime.fromtimestamp(t / 1000, IST) for t in times]
    daily = all(s.time() == dt.time(0, 0) for s in stamps)
    series = _series(group)
    rows: list[ChartinkRow] = []
    for index in reversed(range(len(stamps))):
        values: dict[str, Value] = {}
        for alias, points in series.items():
            # Series and times line up from the latest bar backwards.
            position = len(points) - len(stamps) + index
            values[alias] = _value(points[position]) if 0 <= position < len(points) else None
        label = stamps[index].strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M")
        rows.append(ChartinkRow(label, values))
    return rows


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
        columns=_column_specs(scan.get("atlas_json")),
    )


def _column_specs(atlas_json: Any) -> dict[str, ColumnSpec]:
    """
    Column names and colours from the screener's editor state
    (`atlas_json.columns.children`). Best effort: a screener without it, or a
    changed shape, just has no names - the scan itself still runs.
    """
    try:
        atlas = json.loads(atlas_json) if isinstance(atlas_json, str) else atlas_json
        children: list[Any] = atlas["columns"]["children"]
    except (ValueError, KeyError, TypeError):
        return {}
    specs: dict[str, ColumnSpec] = {}
    for child in children:
        if not isinstance(child, dict) or child.get("isEnabled") is False or not child.get("id"):
            continue
        filters = (child.get("colorFilters") or {}).get("children") or []
        colors = tuple(
            str(f["color"]) if isinstance(f, dict) and f.get("color") else None for f in filters
        )
        column = column_key(str(child["id"]))
        specs[column] = ColumnSpec(name=str(child.get("name") or column), colors=colors)
    return specs


def _widget(raw: dict[str, Any]) -> WidgetDef:
    details = raw.get("jsondetails")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = None
    result_type = details.get("resultType") if isinstance(details, dict) else None
    groups = details.get("groups") if isinstance(details, dict) else None
    size = groups.get("size") if isinstance(groups, dict) else None
    return WidgetDef(
        id=int(raw.get("id", 0)),
        name=str(raw.get("name") or f"Widget {raw.get('id')}"),
        query=str(raw["query"]),
        result_type=str(result_type) if result_type else None,
        size=int(size) if isinstance(size, int | str) and str(size).isdigit() else None,
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
    if isinstance(value, float) and not abs(value) < _NO_VALUE:  # also NaN
        return None
    if isinstance(value, int | float):
        return value
    return str(value)
