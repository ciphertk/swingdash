"""
The Securities tab's three views - stocks, indices, ETFs - as data: columns,
cell rendering, sort keys and what the text filter searches.

Colour follows the dashboard's few semantic colours: surveillance is red
(a real trading restriction), tight 2%/5% bands amber (caution), index
moves green/red, everything else neutral.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.text import Text

from swingdash.domain.securities import Equity, Etf, IndexRow, PriceBand, SecuritiesSnapshot

_BAND_ORDER = {band: i for i, band in enumerate(PriceBand)}
_TIGHT_BANDS = {PriceBand.P2, PriceBand.P5}


@dataclass(frozen=True)
class Column[R]:
    key: str
    header: str
    width: int
    # Value to sort by, or None if the column isn't sortable. Rows whose value
    # is None always sink to the bottom.
    sort: Callable[[R], object] | None = None


@dataclass(frozen=True)
class View[R]:
    id: str
    title: str
    columns: tuple[Column[R], ...]
    rows: Callable[[SecuritiesSnapshot], Sequence[R]]
    # Unique per row, and the plain text of the first column.
    key: Callable[[R], str]
    cells: Callable[[R], tuple[Text, ...]]
    search_text: Callable[[R], str]
    band: Callable[[R], PriceBand | None] | None = None
    flagged: Callable[[R], bool] | None = None

    @property
    def sortable(self) -> tuple[Column[R], ...]:
        return tuple(c for c in self.columns if c.sort is not None)

    def visible(
        self,
        snapshot: SecuritiesSnapshot,
        text: str,
        band: PriceBand | None,
        flagged_only: bool,
    ) -> list[R]:
        rows = self.rows(snapshot)
        if text:
            needle = text.upper()
            rows = [r for r in rows if needle in self.search_text(r)]
        if band is not None and self.band is not None:
            band_of = self.band
            rows = [r for r in rows if band_of(r) is band]
        if flagged_only and self.flagged is not None:
            flagged = self.flagged
            rows = [r for r in rows if flagged(r)]
        return list(rows)

    def ordered(self, rows: Sequence[R], column: Column[R], descending: bool) -> list[R]:
        sort = column.sort
        if sort is None:
            return list(rows)
        present = [r for r in rows if sort(r) is not None]
        missing = sorted((r for r in rows if sort(r) is None), key=self.key)
        present.sort(key=sort, reverse=descending)  # type: ignore[arg-type]
        return present + missing


# --- cells --------------------------------------------------------------------


def _plain(value: str | None, style: str = "") -> Text:
    return Text(value, style=style) if value else Text("-", style="grey50")


def _number(value: float | None, fmt: str, style: str = "") -> Text:
    if value is None:
        return Text("-", style="grey50", justify="right")
    return Text(fmt.format(value), style=style, justify="right")


def _change(value: float | None) -> Text:
    if value is None:
        return Text("-", style="grey50", justify="right")
    return Text(f"{value:+.2f}", style="green" if value >= 0 else "red", justify="right")


def _band(band: PriceBand | None) -> Text:
    if band is None:
        return Text("-", style="grey50")
    if band is PriceBand.NO_BAND:
        return Text(band.label, style="grey62")
    return Text(band.label, style="yellow" if band in _TIGHT_BANDS else "")


def _listed(date: dt.date | None) -> Text:
    return Text(f"{date:%d %b %Y}", style="grey62") if date else Text("-", style="grey50")


def _stock_cells(e: Equity) -> tuple[Text, ...]:
    return (
        Text(e.symbol, style="bold"),
        _plain(e.name),
        _plain(e.sector),
        _number(e.market_cap_cr, "{:,.0f}"),
        _band(e.band),
        Text(" ".join(e.surveillance.labels()), style="red"),
        _listed(e.listed_on),
    )


def _index_cells(i: IndexRow) -> tuple[Text, ...]:
    return (
        Text(i.name, style="bold"),
        _plain(i.category, "grey62"),
        _number(i.last, "{:,.2f}"),
        _change(i.change_pct),
        _number(i.pe, "{:.1f}", "grey62"),
        _number(i.pb, "{:.2f}", "grey62"),
        _number(i.dividend_yield, "{:.2f}", "grey62"),
        _number(i.year_high, "{:,.2f}", "grey62"),
        _number(i.year_low, "{:,.2f}", "grey62"),
        Text(
            f"{i.advances}/{i.declines}" if i.advances is not None else "-",
            style="grey62" if i.advances is not None else "grey50",
            justify="right",
        ),
    )


def _etf_cells(e: Etf) -> tuple[Text, ...]:
    return (
        Text(e.symbol, style="bold"),
        _plain(e.name),
        _plain(e.underlying),
        _plain(e.asset_class.title(), "grey62"),
        _band(e.band),
        _listed(e.listed_on),
    )


def _severity(e: Equity) -> int | None:
    """Sort surveillance by how many measures apply; unflagged rows sink."""
    labels = e.surveillance.labels()
    return len(labels) or None


def _band_rank(band: PriceBand | None) -> int | None:
    return _BAND_ORDER[band] if band is not None else None


def _lower(value: str | None) -> str | None:
    return value.lower() if value else None


STOCKS = View[Equity](
    id="stocks",
    title="Stocks",
    columns=(
        Column("symbol", "SYMBOL", 12, lambda e: e.symbol),
        Column("name", "NAME", 36, lambda e: e.name.lower()),
        Column("sector", "SECTOR", 26, lambda e: _lower(e.sector)),
        Column("mcap", "MCAP CR", 12, lambda e: e.market_cap_cr),
        Column("band", "BAND", 5, lambda e: _band_rank(e.band)),
        Column("surveillance", "SURVEILLANCE", 18, _severity),
        Column("listed", "LISTED", 11, lambda e: e.listed_on),
    ),
    rows=lambda s: s.equities,
    key=lambda e: e.symbol,
    cells=_stock_cells,
    search_text=lambda e: f"{e.symbol} {e.name} {e.sector or ''}".upper(),
    band=lambda e: e.band,
    flagged=lambda e: e.surveillance.flagged,
)

INDICES = View[IndexRow](
    id="indices",
    title="Indices",
    columns=(
        Column("name", "INDEX", 36, lambda i: i.name),
        Column("category", "CATEGORY", 13, lambda i: i.category),
        Column("last", "CLOSE", 11, lambda i: i.last),
        Column("change", "CHG%", 7, lambda i: i.change_pct),
        Column("pe", "P/E", 6, lambda i: i.pe),
        Column("pb", "P/B", 6, lambda i: i.pb),
        Column("dy", "DIV Y", 6, lambda i: i.dividend_yield),
        Column("high", "52W HIGH", 11),
        Column("low", "52W LOW", 11),
        Column("breadth", "ADV/DEC", 8),
    ),
    rows=lambda s: s.indices,
    key=lambda i: i.name,
    cells=_index_cells,
    search_text=lambda i: f"{i.name} {i.category}".upper(),
)

ETFS = View[Etf](
    id="etfs",
    title="ETFs",
    columns=(
        Column("symbol", "SYMBOL", 12, lambda e: e.symbol),
        Column("name", "NAME", 46, lambda e: e.name.lower()),
        Column("underlying", "UNDERLYING", 28, lambda e: e.underlying.lower()),
        Column("class", "CLASS", 10, lambda e: e.asset_class.lower()),
        Column("band", "BAND", 5, lambda e: _band_rank(e.band)),
        Column("listed", "LISTED", 11, lambda e: e.listed_on),
    ),
    rows=lambda s: s.etfs,
    key=lambda e: e.symbol,
    cells=_etf_cells,
    search_text=lambda e: f"{e.symbol} {e.name} {e.underlying} {e.asset_class}".upper(),
    band=lambda e: e.band,
)

VIEWS: tuple[View[Equity] | View[IndexRow] | View[Etf], ...] = (STOCKS, INDICES, ETFS)

# `b` cycles through these; None means every band.
BAND_FILTERS: tuple[PriceBand | None, ...] = (
    None,
    PriceBand.P2,
    PriceBand.P5,
    PriceBand.P10,
    PriceBand.P20,
    PriceBand.NO_BAND,
)
