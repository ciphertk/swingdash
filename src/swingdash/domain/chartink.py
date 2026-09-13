"""
Chartink screeners and dashboard widgets, as swingdash understands them.

Chartink has no public API. Its website runs a screener by posting a scan
clause, and a dashboard widget by posting a query; swingdash replays those
same requests. This module is the source-independent part: what a request
and a result look like, and turning whatever the user pastes - a link, a
payload copied from the browser's network tab, a Python dict, a bare clause
- into a request.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast
from urllib.parse import parse_qsl

Value = float | int | str | None

# Widget requests only need the latest value of each column; Chartink sends a
# point per bar otherwise. 1000 rows is honoured (verified Sep 2026).
WIDGET_DEFAULTS = {"use_live": "1", "limit": "1000", "size": "1"}

_KNOWN_FIELDS = (
    "scan_clause",
    "debug_clause",
    "column_clause",
    "query",
    "use_live",
    "limit",
    "size",
)
_URL = re.compile(
    r"^https?://(?:www\.)?chartink\.com/(?P<kind>screener|dashboard)/(?P<slug>[\w-]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)


class ChartinkKind(StrEnum):
    SCREENER = "screener"
    WIDGET = "widget"


class ChartinkInputError(ValueError):
    """What the user pasted can't be turned into a Chartink request."""


@dataclass(frozen=True)
class ChartinkRequest:
    kind: ChartinkKind
    fields: dict[str, str]


@dataclass(frozen=True)
class ImportTarget:
    """A Chartink link to read a screener's clause or a dashboard's widgets from."""

    kind: Literal["screener", "dashboard"]
    url: str
    slug: str


@dataclass(frozen=True)
class ChartinkRow:
    # The symbol for stock lists; a sector/industry name for aggregate widgets.
    key: str
    values: dict[str, Value]


@dataclass(frozen=True)
class ChartinkResult:
    columns: tuple[str, ...]
    rows: tuple[ChartinkRow, ...]
    # "symbol" for stock lists, e.g. "sector" for grouped widgets, None for one aggregate.
    group_by: str | None
    # When Chartink's data was last updated, if it says.
    data_time: dt.datetime | None = None
    # Rows Chartink had before `limit` applied, if it says (widgets only).
    available: int | None = None

    @property
    def is_stock_list(self) -> bool:
        return self.group_by == "symbol"


@dataclass(frozen=True)
class WidgetDef:
    id: int
    name: str
    query: str
    result_type: str | None  # table | barchart | areachart ...

    @property
    def is_table(self) -> bool:
        return self.result_type == "table"

    def request(self) -> ChartinkRequest:
        return ChartinkRequest(ChartinkKind.WIDGET, {"query": self.query, **WIDGET_DEFAULTS})


@dataclass(frozen=True)
class DashboardDef:
    id: int
    name: str
    is_private: bool
    widgets: tuple[WidgetDef, ...]


@dataclass(frozen=True)
class ScreenerDef:
    id: int | None
    name: str
    slug: str
    is_private: bool
    clause: str | None

    def request(self) -> ChartinkRequest:
        if not self.clause:
            raise ChartinkInputError(f"Screener '{self.name}' has no scan clause to run.")
        return ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": self.clause})


def parse_user_input(text: str) -> ChartinkRequest | ImportTarget:
    """
    Accepts, in order of precedence:
    - a chartink.com/screener/<slug> or /dashboard/<id> link;
    - a JSON object or Python dict ({'scan_clause': '...'});
    - a form-encoded body (DevTools payload "view source");
    - `field: value` lines (DevTools payload "view parsed");
    - a bare scan clause "( {57960} ( ... ) )" or widget query "select ...".
    """
    stripped = text.strip()
    if not stripped:
        raise ChartinkInputError("Paste a Chartink link, a payload, or a scan clause.")

    url = _URL.match(stripped)
    if url:
        kind: Literal["screener", "dashboard"] = (
            "screener" if url.group("kind").lower() == "screener" else "dashboard"
        )
        slug = url.group("slug")
        return ImportTarget(kind, f"https://chartink.com/{kind}/{slug}", slug)
    if re.match(r"^https?://", stripped, re.IGNORECASE):
        raise ChartinkInputError(
            "Only chartink.com/screener/... and chartink.com/dashboard/... links can be imported."
        )

    fields = (
        _from_mapping_literal(stripped)
        or _from_form_encoded(stripped)
        or _from_field_lines(stripped)
        or _from_bare(stripped)
    )
    if not fields:
        raise ChartinkInputError(
            "Couldn't find a scan_clause or query in that - paste the request payload from "
            "the browser's network tab, a scan clause, or a chartink.com link."
        )
    return _to_request(fields)


def _to_request(fields: dict[str, str]) -> ChartinkRequest:
    if fields.get("query", "").strip():
        # size=1: only the latest value per column is shown, whatever was pasted.
        merged = {**WIDGET_DEFAULTS, **fields, "size": "1"}
        return ChartinkRequest(ChartinkKind.WIDGET, merged)
    if fields.get("scan_clause", "").strip():
        return ChartinkRequest(ChartinkKind.SCREENER, fields)
    raise ChartinkInputError("The payload has no scan_clause (screener) or query (widget).")


def _only_known(pairs: dict[str, object]) -> dict[str, str]:
    return {k: str(v) for k, v in pairs.items() if k in _KNOWN_FIELDS and v is not None}


def _from_mapping_literal(text: str) -> dict[str, str] | None:
    if not text.startswith("{"):
        return None
    for load in (json.loads, ast.literal_eval):
        try:
            value = load(text)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict):
            items = cast(dict[object, object], value)
            return _only_known({str(k): v for k, v in items.items()}) or None
    return None


def _from_form_encoded(text: str) -> dict[str, str] | None:
    if "\n" in text or not re.match(r"^(" + "|".join(_KNOWN_FIELDS) + r")=", text):
        return None
    return _only_known(dict(parse_qsl(text, keep_blank_values=True))) or None


def _from_field_lines(text: str) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^\s*(" + "|".join(_KNOWN_FIELDS) + r")\s*:\s*(.*)$", line)
        if match:
            current = str(match.group(1))
            fields[current] = str(match.group(2)).strip()
        elif current is not None and line.strip():
            fields[current] = f"{fields[current]} {line.strip()}"  # a wrapped value
    return fields or None


def _from_bare(text: str) -> dict[str, str] | None:
    single_line = " ".join(text.split())
    if single_line.lower().startswith("select "):
        return {"query": single_line}
    if single_line.startswith("("):
        return {"scan_clause": single_line}
    return None
