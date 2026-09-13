"""
How Chartink results render: which columns show, their headers, cell text
and sort keys. Chartink columns are whatever the screener/widget defines,
so this works from the column names rather than a fixed table.
"""

from __future__ import annotations

from rich.text import Text

from swingdash.domain.chartink import ChartinkResult, Value
from swingdash.domain.metrics.burst_score import BurstScoreResult
from swingdash.domain.securities import PriceBand

# Stock lists already show the symbol as their first column.
HIDDEN_FOR_STOCKS = frozenset({"nsecode", "bsecode"})
NO_GROUPS = "*no-groups*"

KEY_COLUMN = "_key"
BAND_COLUMN = "_band"
BURST_COLUMN = "_burst"

_HEADERS = {
    "name": "NAME",
    "close": "CLOSE",
    "per_chg": "% CHG",
    "volume": "VOLUME",
}
_TIGHT_BANDS = {PriceBand.P2, PriceBand.P5}
_BURST_STYLE = {"strong": "green", "moderate": "yellow", "weak": "red"}


def data_columns(result: ChartinkResult) -> tuple[str, ...]:
    if result.is_stock_list:
        return tuple(c for c in result.columns if c not in HIDDEN_FOR_STOCKS)
    return result.columns


def key_header(result: ChartinkResult) -> str:
    return (result.group_by or "group").upper()


def header(column: str) -> str:
    return _HEADERS.get(column, column.upper())


def key_text(key: str) -> str:
    return "all" if key == NO_GROUPS else key


def key_cell(key: str, chartable: bool) -> Text:
    # Symbols are underlined like everywhere else a row opens a chart.
    return Text(key_text(key), style="bold underline" if chartable else "")


def _missing() -> Text:
    return Text("-", style="grey50", justify="right")


def _is_change(column: str) -> bool:
    lowered = column.lower()
    return "chg" in lowered or "change" in lowered or lowered.endswith("%")


def value_cell(column: str, value: Value) -> Text:
    if value is None or value == "":
        return _missing()
    if isinstance(value, str):
        return Text(value)
    if _is_change(column):
        return Text(f"{value:+,.2f}", style="green" if value >= 0 else "red", justify="right")
    if isinstance(value, int):
        return Text(f"{value:,}", justify="right")
    return Text(f"{value:,.2f}", justify="right")


def band_cell(band: PriceBand | None) -> Text:
    if band is None:
        return _missing()
    return Text(band.label, style="yellow" if band in _TIGHT_BANDS else "", justify="right")


def burst_cell(burst: BurstScoreResult | None) -> Text:
    if burst is None:
        return _missing()
    text = Text(f"{burst.power_score:>3} ", justify="right")
    text.append("●", style=_BURST_STYLE.get(burst.classification, ""))
    return text


def sort_key(value: object) -> tuple[int, float | str]:
    """Numbers before text, text case-insensitively; callers sink None themselves."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return (0, float(value))
    return (1, str(value).lower())
