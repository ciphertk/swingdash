"""
The positions table's columns, for open and closed positions: header, the
raw value (CSV export) and how that value renders.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text

from swingdash.domain.risk.portfolio import Position
from swingdash.services.risk import PositionRow
from swingdash.ui.tabs.risk.format import grouped, inr, short_date, signed_inr


@dataclass(frozen=True)
class Column[R]:
    key: str
    header: str
    value: Callable[[R, float], object]  # (row, capital) -> raw value
    cell: Callable[[R, float], Text]


def _missing() -> Text:
    return Text("-", style="grey50", justify="right")


def _number(value: object, fmt: str = "{:,.2f}", style: str = "") -> Text:
    if not isinstance(value, int | float):
        return _missing()
    return Text(fmt.format(value), style=style, justify="right")


def _signed(value: object, fmt: Callable[[float], str] = "{:+,.2f}".format) -> Text:
    if not isinstance(value, int | float):
        return _missing()
    return Text(fmt(value), style="green" if value >= 0 else "red", justify="right")


def _r_text(value: float) -> str:
    return f"{value:+.1f}R"


def _price(row: PositionRow) -> float | None:
    return row.quote.price if row.quote else None


def _pnl_pct(position: Position, price: float | None) -> float | None:
    return None if price is None else (price / position.entry - 1) * 100


# --- open positions -----------------------------------------------------------


def _pnl(row: PositionRow, _: float) -> float | None:
    price = _price(row)
    return None if price is None else row.position.pnl(price)


def _open_pnl_pct(row: PositionRow, _: float) -> float | None:
    return _pnl_pct(row.position, _price(row))


def _open_r(row: PositionRow, _: float) -> float | None:
    price = _price(row)
    return None if price is None else row.position.r_multiple(price)


def _risk_pct(row: PositionRow, capital: float) -> float | None:
    return row.position.open_risk / capital * 100 if capital > 0 else None


def _to_stop(row: PositionRow, _: float) -> float | None:
    price = _price(row)
    return None if price is None else row.position.giveback(price)


def _ltp_cell(row: PositionRow, _: float) -> Text:
    # A cached daily close is dimmed: it isn't today's price.
    stale = row.quote is not None and row.quote.source == "close"
    return _number(_price(row), style="grey62" if stale else "")


def _stop_cell(row: PositionRow, _: float) -> Text:
    position = row.position
    return _number(position.stop, style="green" if position.stop >= position.entry else "")


def _risk_cell(row: PositionRow, _: float) -> Text:
    risk = row.position.open_risk
    return Text(inr(risk), style="" if risk else "grey50", justify="right")


OPEN_COLUMNS: tuple[Column[PositionRow], ...] = (
    Column(
        "symbol",
        "SYMBOL",
        lambda r, _: r.position.symbol,
        lambda r, _: Text(r.position.symbol, style="bold underline"),
    ),
    Column(
        "qty",
        "QTY",
        lambda r, _: r.position.quantity,
        lambda r, _: Text(grouped(r.position.quantity), justify="right"),
    ),
    Column("entry", "ENTRY", lambda r, _: r.position.entry, lambda r, _: _number(r.position.entry)),
    Column("stop", "STOP", lambda r, _: r.position.stop, _stop_cell),
    Column("ltp", "LTP", lambda r, _: _price(r), _ltp_cell),
    Column("pnl", "P&L", _pnl, lambda r, c: _signed(_pnl(r, c), signed_inr)),
    Column("pnl_pct", "P&L %", _open_pnl_pct, lambda r, c: _signed(_open_pnl_pct(r, c))),
    Column("r", "R", _open_r, lambda r, c: _signed(_open_r(r, c), _r_text)),
    Column("risk", "RISK", lambda r, _: r.position.open_risk, _risk_cell),
    Column("risk_pct", "RISK %", _risk_pct, lambda r, c: _number(_risk_pct(r, c))),
    Column("to_stop", "TO STOP", _to_stop, lambda r, c: _signed(_to_stop(r, c), signed_inr)),
    Column(
        "taken",
        "TAKEN",
        lambda r, _: r.position.opened_on.isoformat(),
        lambda r, _: Text(short_date(r.position.opened_on)),
    ),
    Column(
        "days",
        "DAYS",
        lambda r, _: r.days_held,
        lambda r, _: _number(r.days_held, "{:d}"),
    ),
)


# --- closed positions ---------------------------------------------------------


def _realised_pct(position: Position, _: float) -> float | None:
    return _pnl_pct(position, position.exit_price)


def _closed_r(position: Position, _: float) -> float | None:
    return None if position.exit_price is None else position.r_multiple(position.exit_price)


def _days_held(position: Position, _: float) -> int | None:
    return None if position.closed_on is None else (position.closed_on - position.opened_on).days


CLOSED_COLUMNS: tuple[Column[Position], ...] = (
    Column(
        "symbol",
        "SYMBOL",
        lambda p, _: p.symbol,
        lambda p, _: Text(p.symbol, style="bold underline"),
    ),
    Column(
        "qty",
        "QTY",
        lambda p, _: p.quantity,
        lambda p, _: Text(grouped(p.quantity), justify="right"),
    ),
    Column("entry", "ENTRY", lambda p, _: p.entry, lambda p, _: _number(p.entry)),
    Column("exit", "EXIT", lambda p, _: p.exit_price, lambda p, _: _number(p.exit_price)),
    Column(
        "pnl", "P&L", lambda p, _: p.realised_pnl, lambda p, _: _signed(p.realised_pnl, signed_inr)
    ),
    Column("pnl_pct", "P&L %", _realised_pct, lambda p, c: _signed(_realised_pct(p, c))),
    Column("r", "R", _closed_r, lambda p, c: _signed(_closed_r(p, c), _r_text)),
    Column(
        "taken",
        "TAKEN",
        lambda p, _: p.opened_on.isoformat(),
        lambda p, _: Text(short_date(p.opened_on)),
    ),
    Column(
        "exited",
        "EXITED",
        lambda p, _: p.closed_on.isoformat() if p.closed_on else None,
        lambda p, _: Text(short_date(p.closed_on)) if p.closed_on else _missing(),
    ),
    Column("days", "DAYS", _days_held, lambda p, c: _number(_days_held(p, c), "{:d}")),
)
