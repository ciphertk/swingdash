"""
The Scanner table's columns as data: header, width, how a row renders, what
it sorts by, and the raw value CSV export writes.

Adding a metric's columns means adding entries here - the tab iterates this.

Colour follows the dashboard's semantic colours and the Pine scripts' own
dots: green strong, yellow moderate/neutral, red weak.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text

from swingdash.domain.scanner import ScannerRow

_CLASS_STYLE = {"strong": "green", "moderate": "yellow", "neutral": "yellow", "weak": "red"}


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    width: int
    cell: Callable[[ScannerRow], Text]
    # None if the column isn't sortable; rows whose value is None sink.
    sort: Callable[[ScannerRow], float | str | None] | None = None
    descending_first: bool = True


def _missing() -> Text:
    return Text("-", style="grey50", justify="right")


def _number(value: float | None, fmt: str, style: str = "") -> Text:
    return _missing() if value is None else Text(fmt.format(value), style=style, justify="right")


def _signed(value: float | None, fmt: str = "{:+.2f}") -> Text:
    if value is None:
        return _missing()
    return Text(fmt.format(value), style="green" if value >= 0 else "red", justify="right")


def _symbol(row: ScannerRow) -> Text:
    # Plain text must stay exactly the symbol: LiveTable sorts rows by it.
    style = "bold underline" if row.metrics is not None else "grey50 underline"
    return Text(row.symbol, style=style)


def _burst(row: ScannerRow) -> Text:
    if row.metrics is None:
        return _missing()
    burst = row.metrics.burst
    text = Text(f"{burst.power_score:>3} ", justify="right")
    text.append("●", style=_CLASS_STYLE.get(burst.classification, ""))
    return text


def _count(pick: Callable[[ScannerRow], int]) -> Callable[[ScannerRow], Text]:
    def cell(row: ScannerRow) -> Text:
        if row.metrics is None:
            return _missing()
        value = pick(row)
        return Text(str(value), style="" if value else "grey50", justify="right")

    return cell


def _mswing(row: ScannerRow) -> Text:
    score = row.metrics.mswing.score if row.metrics else None
    if score is None:
        return _missing()
    return Text(
        f"{score:+.2f}", style=_CLASS_STYLE.get(row.mswing_class or "", ""), justify="right"
    )


COLUMNS: tuple[Column, ...] = (
    Column("symbol", "SYMBOL", 12, _symbol, sort=lambda r: r.symbol, descending_first=False),
    Column("ltp", "LTP", 10, lambda r: _number(r.ltp, "{:,.2f}")),
    Column("change", "CHG%", 7, lambda r: _signed(r.change_pct), sort=lambda r: r.change_pct),
    Column(
        "burst",
        "BURST",
        6,
        _burst,
        sort=lambda r: r.metrics.burst.power_score if r.metrics else None,
    ),
    Column("c5", "5%", 4, _count(lambda r: r.metrics.burst.count_5pct if r.metrics else 0)),
    Column(
        "c10",
        "10%",
        4,
        _count(lambda r: r.metrics.burst.count_10pct if r.metrics else 0),
        sort=lambda r: r.metrics.burst.count_10pct if r.metrics else None,
    ),
    Column(
        "c19",
        "19%",
        4,
        _count(lambda r: r.metrics.burst.count_19pct if r.metrics else 0),
        sort=lambda r: r.metrics.burst.count_19pct if r.metrics else None,
    ),
    Column(
        "max",
        "MAX%",
        6,
        lambda r: _number(r.metrics.burst.max_move_pct if r.metrics else None, "{:.1f}"),
        sort=lambda r: r.metrics.burst.max_move_pct if r.metrics else None,
    ),
    Column(
        "mswing",
        "MSWING",
        7,
        _mswing,
        sort=lambda r: r.metrics.mswing.score if r.metrics else None,
    ),
    Column(
        "ema",
        "EMA9",
        7,
        lambda r: _number(r.metrics.mswing.ema if r.metrics else None, "{:+.2f}", "grey62"),
    ),
    Column("vs", "vs IDX", 7, lambda r: _signed(r.vs_index), sort=lambda r: r.vs_index),
)

SORTABLE: tuple[Column, ...] = tuple(c for c in COLUMNS if c.sort is not None)
DEFAULT_SORT = next(i for i, c in enumerate(SORTABLE) if c.key == "mswing")
