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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, cast
from urllib.parse import parse_qsl

Value = float | int | str | None

# A widget's `size` is how many bars of history each value comes with, and
# `limit` how many groups. Grouped widgets (GROUP BY symbol/sector/...) show
# only the latest bar - asking for history there returns `[]` for big ones.
# Widgets without GROUP BY are one series over time (market breadth, MBI):
# their history is the data, one row per bar. 1000 groups is honoured, 375
# is Chartink's default history (verified Sep 2026).
WIDGET_DEFAULTS = {"use_live": "1", "limit": "1000", "size": "1"}
TREND_SIZE = "375"
_GROUP_BY = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)


def is_grouped_query(query: str) -> bool:
    return bool(_GROUP_BY.search(query))


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


# A screener's columns come back as 'scan-column-<id>', each with a colour
# flag '<id>-conditional-filters-color'. Built-in columns have fixed ids;
# they're renamed to the plain names a screener without columns returns.
_BUILTIN_COLUMNS = {
    "default-close": "close",
    "default-percent-change": "per_chg",
    "default-volume": "volume",
}
_COLOR_SUFFIX = ":color"


def column_key(column_id: str) -> str:
    """The result column for a Chartink column id ('default-close' -> 'close')."""
    return _BUILTIN_COLUMNS.get(column_id, column_id)


def color_key(column: str) -> str:
    """Where a row keeps a column's colour flag (1-based index into its colours)."""
    return column + _COLOR_SUFFIX


def is_builtin_column(column: str) -> bool:
    return column in _BUILTIN_COLUMNS.values()


@dataclass(frozen=True)
class ColumnSpec:
    """How a screener's own column is named and coloured on Chartink."""

    name: str
    # Chartink's colour flag N picks colors[N-1]; the last is the "otherwise"
    # case and often None (no colour). Hex, e.g. "#4CAF50FF".
    colors: tuple[str | None, ...] = ()

    def color(self, flag: Value) -> str | None:
        if not isinstance(flag, int) or not 1 <= flag <= len(self.colors):
            return None
        return self.colors[flag - 1]


class ChartinkKind(StrEnum):
    SCREENER = "screener"
    WIDGET = "widget"


class ChartinkInputError(ValueError):
    """What the user pasted can't be turned into a Chartink request."""


@dataclass(frozen=True)
class ChartinkRequest:
    kind: ChartinkKind
    fields: dict[str, str]

    def runnable(self) -> ChartinkRequest:
        """
        The request to actually send. Widgets: a grouped one asks for just
        the latest bar; an ungrouped one for its history (TREND_SIZE unless
        it names its own). Items saved before this rule get it too.
        """
        if self.kind is not ChartinkKind.WIDGET:
            return self
        fields = {**WIDGET_DEFAULTS, **self.fields}
        if is_grouped_query(fields.get("query", "")):
            fields["size"] = "1"
        elif fields.get("size", "").strip() in ("", "0", "1"):
            fields["size"] = TREND_SIZE
        return self if fields == self.fields else ChartinkRequest(self.kind, fields)


@dataclass(frozen=True)
class ImportTarget:
    """A Chartink link to read a screener's clause or a dashboard's widgets from."""

    kind: Literal["screener", "dashboard"]
    url: str
    slug: str
    # A screener's request payload pasted along with its link - the only way
    # to get its custom columns, which a link alone can't bring.
    payload: ChartinkRequest | None = None


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
    size: int | None = None  # bars of history the widget shows on Chartink

    @property
    def is_table(self) -> bool:
        return self.result_type == "table"

    def request(self) -> ChartinkRequest:
        fields = {"query": self.query, **WIDGET_DEFAULTS}
        if self.size and self.size > 0:
            fields["size"] = str(self.size)
        return ChartinkRequest(ChartinkKind.WIDGET, fields).runnable()


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
    # By result column (see column_key). Chartink builds the matching
    # `column_clause` in the browser, so a link alone can't request these.
    columns: dict[str, ColumnSpec] = field(default_factory=dict[str, ColumnSpec])

    @property
    def custom_columns(self) -> list[str]:
        """Names of the columns beyond Chartink's built-in close/%change/volume."""
        return [spec.name for key, spec in self.columns.items() if not is_builtin_column(key)]

    def request(self) -> ChartinkRequest:
        if not self.clause:
            raise ChartinkInputError(f"Screener '{self.name}' has no scan clause to run.")
        return ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": self.clause})


@dataclass(frozen=True)
class ChartinkItem:
    """A saved screener or widget, with its last result."""

    id: int
    name: str
    request: ChartinkRequest
    source_url: str | None
    # The dashboard an imported widget belongs to; None for standalone items.
    collection: str | None
    result: ChartinkResult | None
    fetched_at: dt.datetime | None
    # Why the latest run failed; the last good result is kept alongside it.
    error: str | None = None
    # Names/colours of the screener's own columns, when imported from its page.
    columns: dict[str, ColumnSpec] = field(default_factory=dict[str, ColumnSpec])

    @property
    def missing_columns(self) -> list[str]:
        """Custom columns the screener has that this item doesn't request."""
        if "column_clause" in self.request.fields:
            return []
        return [spec.name for key, spec in self.columns.items() if not is_builtin_column(key)]


def parse_user_input(text: str) -> ChartinkRequest | ImportTarget:
    """
    Accepts, in order of precedence:
    - a chartink.com/screener/<slug> or /dashboard/<id> link - a screener
      link may be followed, on the next lines, by that screener's payload;
    - a JSON object or Python dict ({'scan_clause': '...'});
    - a form-encoded body (DevTools payload "view source");
    - `field: value` lines (DevTools payload "view parsed");
    - a bare scan clause "( {57960} ( ... ) )" or widget query "select ...".
    """
    stripped = text.strip()
    if not stripped:
        raise ChartinkInputError("Paste a Chartink link, a payload, or a scan clause.")

    first_line, _, rest = stripped.partition("\n")
    url = _URL.match(first_line.strip())
    if url:
        kind: Literal["screener", "dashboard"] = (
            "screener" if url.group("kind").lower() == "screener" else "dashboard"
        )
        slug = url.group("slug")
        link = f"https://chartink.com/{kind}/{slug}"
        if not rest.strip():
            return ImportTarget(kind, link, slug)
        payload = parse_user_input(rest)
        if kind != "screener" or not isinstance(payload, ChartinkRequest):
            raise ChartinkInputError(
                "Only a screener link can be followed by a payload - paste a dashboard "
                "link on its own."
            )
        if payload.kind is not ChartinkKind.SCREENER:
            raise ChartinkInputError("That payload is a widget's, not a screener's.")
        return ImportTarget(kind, link, slug, payload)
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
        merged = {**WIDGET_DEFAULTS, **fields}
        return ChartinkRequest(ChartinkKind.WIDGET, merged).runnable()
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
