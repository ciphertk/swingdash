"""
The detail panel under the Scanner table: the highlighted symbol's full
Burst Power table and Mswing breakdown, laid out like the TradingView
indicators they come from.
"""

from __future__ import annotations

import datetime as dt

from rich.table import Table
from rich.text import Text

from swingdash.domain.scanner import ScannerIndex, ScannerRow

_DOT = {"strong": "green", "moderate": "yellow", "weak": "red"}
_CLASS_STYLE = {"strong": "green", "neutral": "yellow", "weak": "red"}


def _date(iso: str | None) -> Text:
    # Pine's table format: yyyy-MMM-dd.
    if not iso:
        return Text("nope", style="red")
    return Text(dt.date.fromisoformat(iso).strftime("%Y-%b-%d"), style="grey62")


def burst_panel(row: ScannerRow | None) -> Table | Text:
    if row is None:
        return Text("")
    if row.metrics is None:
        return Text(f"{row.symbol}  loading history...", style="grey50")
    burst = row.metrics.burst
    table = Table(
        title=Text.assemble((row.symbol, "bold"), ("  Burst Power (3Y)", "grey62")),
        title_justify="left",
        box=None,
        padding=(0, 2, 0, 0),
        show_edge=False,
    )
    table.add_column("Move", style="grey62")
    table.add_column("Count", justify="right")
    table.add_column("Latest")
    max_move = burst.max_move_pct
    table.add_row(
        "Max%",
        Text(f"{max_move:.2f}%", style="green") if max_move is not None else Text("nope", "red"),
        _date(burst.max_move_date),
    )
    for label, count, last in (
        ("05% +", burst.count_5pct, burst.last_date_5pct),
        ("10% +", burst.count_10pct, burst.last_date_10pct),
        ("19% +", burst.count_19pct, burst.last_date_19pct),
    ):
        table.add_row(label, Text(str(count), style="" if count else "red"), _date(last))
    power = Text(str(burst.power_score), style="bold")
    table.add_row(
        Text("Burst Power", style="bold"), power, Text("●", style=_DOT[burst.classification])
    )
    return table


def mswing_panel(row: ScannerRow | None, index: ScannerIndex) -> Text:
    if row is None or row.metrics is None:
        return Text("")
    mswing = row.metrics.mswing
    index_score = index.metrics.mswing.score if index.metrics else None
    text = Text()
    text.append("Mswing  ", style="grey62")
    text.append(_signed(mswing.score), style=_CLASS_STYLE.get(row.mswing_class or "", "bold"))
    text.append(" / ")
    text.append(_signed(index_score), style="bold")
    text.append(f"  {index.name}\n", style="grey62")
    text.append("20d momentum ", style="grey62")
    text.append(_signed(mswing.momentum_short))
    text.append("   50d momentum ", style="grey62")
    text.append(_signed(mswing.momentum_long))
    text.append("\nEMA9 ", style="grey62")
    text.append(_signed(mswing.ema))
    if row.vs_index is not None:
        text.append("   vs index ", style="grey62")
        text.append(_signed(row.vs_index), style="green" if row.vs_index >= 0 else "red")
    text.append("\n")
    text.append(
        _describe(mswing.score, index_score), style=_CLASS_STYLE.get(row.mswing_class or "", "")
    )
    text.append("\n")
    if row.metrics.includes_today:
        text.append("includes today's price", style="cyan")
    elif row.history_through is not None:
        text.append(f"as of {row.history_through:%a %d %b} close", style="yellow")
    return text


def _signed(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}"


def _describe(score: float | None, index_score: float | None) -> str:
    """The Pine table's colour logic, in words."""
    if score is None or index_score is None:
        return "Not enough history to compare with the index."
    beating = score >= index_score
    if score > 0:
        return (
            "Strong: positive and beating the index."
            if beating
            else "Neutral: positive but lagging the index."
        )
    if score < 0:
        return (
            "Neutral: negative but beating the index."
            if beating
            else "Weak: negative and lagging the index."
        )
    return "Weak: flat."
