"""
Positions rebuilt from a broker's trades and holdings (Dhan today).

Brokers report fills, not "positions" the way a swing trader thinks of
them, so this matches them first-in-first-out per stock:
- an **open position** is the lots still held: their quantity, average cost
  and the date of the first of them;
- a **closed position** is what was sold on one day: the quantity, the FIFO
  cost of those shares, the average sell price and the dates.

Only delivery trades count (CNC, and MTF kept apart as its own funding);
intraday round trips aren't swing positions. Holdings are the authority for
what's held: shares bought before the fetched trade history become a lot at
the holding's average cost, dated at the start of the history and noted as
such. Anything the trades can't explain is reported, never guessed.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

NORMAL = "normal"
MTF = "mtf"
_FUNDING_BY_PRODUCT = {"CNC": NORMAL, "MTF": MTF}


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class BrokerTrade:
    trade_id: str
    symbol: str  # NSE trading symbol
    isin: str | None
    side: Side
    product: str  # CNC | MTF | INTRADAY | MARGIN
    quantity: int
    price: float
    time: dt.datetime
    charges: float = 0.0  # all charges on this fill, when reported
    security_id: str | None = None  # the broker's own id for the instrument

    @property
    def funding(self) -> str | None:
        return _FUNDING_BY_PRODUCT.get(self.product.upper())


@dataclass(frozen=True)
class BrokerHolding:
    """Held as of the previous session (today's fills aren't in holdings yet)."""

    symbol: str
    isin: str | None
    quantity: int  # delivery + T1, excluding MTF
    avg_cost: float
    mtf_quantity: int = 0
    security_id: str | None = None


@dataclass(frozen=True)
class BrokerDayPosition:
    """A symbol traded today, as the broker's positions report it."""

    symbol: str
    security_id: str | None
    product: str
    net_quantity: int
    day_buy_quantity: int
    day_sell_quantity: int


@dataclass(frozen=True)
class BrokerAccount:
    client_id: str
    name: str
    token_valid_until: dt.datetime | None


@dataclass(frozen=True)
class BrokerCredentials:
    client_id: str
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class BrokerToken:
    token: str = field(repr=False)
    valid_until: dt.datetime | None


@dataclass(frozen=True)
class ImportedPosition:
    ref: str  # stable across syncs while the position is the same
    symbol: str
    isin: str | None
    funding: str
    quantity: int
    entry: float
    opened_on: dt.date
    closed_on: dt.date | None = None
    exit_price: float | None = None
    charges: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class Reconciled:
    positions: tuple[ImportedPosition, ...]
    mismatches: tuple[str, ...] = ()


@dataclass
class _Lot:
    quantity: int
    price: float
    day: dt.date
    charge_per_share: float
    episode: str  # trade id that started the holding this lot belongs to


@dataclass
class _Exit:
    quantity: int = 0
    cost: float = 0.0
    proceeds: float = 0.0
    charges: float = 0.0
    opened_on: dt.date | None = None
    episode: str = ""


@dataclass
class _Book:
    lots: deque[_Lot] = field(default_factory=deque[_Lot])
    # By (sell day, holding): selling out, re-buying and selling again on one
    # day closes two different holdings.
    exits: dict[tuple[dt.date, str], _Exit] = field(
        default_factory=dict[tuple[dt.date, str], _Exit]
    )
    episode: str | None = None  # the current holding's first trade


def reconcile(
    trades: Iterable[BrokerTrade],
    holdings: Sequence[BrokerHolding],
    *,
    history_from: dt.date,
    today: dt.date,
    trade_date: Callable[[BrokerTrade], dt.date] | None = None,
) -> Reconciled:
    """
    `history_from`: the first day the trades cover. `today`: fills dated
    today aren't reflected in holdings yet. `trade_date` maps a fill's time
    to its trading day (default: its date).
    """
    day_of = trade_date or _date_of
    by_key: dict[tuple[str, str], list[BrokerTrade]] = defaultdict(list)
    isins: dict[str, str] = {}
    for trade in trades:
        funding = trade.funding
        if funding is None:
            continue
        by_key[(trade.symbol, funding)].append(trade)
        if trade.isin:
            isins.setdefault(trade.symbol, trade.isin)

    held: dict[tuple[str, str], BrokerHolding] = {}
    for holding in holdings:
        if holding.isin:
            isins.setdefault(holding.symbol, holding.isin)
        if holding.quantity > 0:
            held[(holding.symbol, NORMAL)] = holding
        if holding.mtf_quantity > 0:
            held[(holding.symbol, MTF)] = holding

    positions: list[ImportedPosition] = []
    mismatches: list[str] = []
    for key in sorted(set(by_key) | set(held)):
        symbol, funding = key
        fills = sorted(by_key.get(key, []), key=lambda t: (t.time, t.trade_id))
        holding = held.get(key)
        held_quantity = 0
        if holding is not None:
            held_quantity = holding.quantity if funding == NORMAL else holding.mtf_quantity
        book = _Book()

        # Shares held before the history starts: the holding explains them.
        before_today = sum(
            (t.quantity if t.side is Side.BUY else -t.quantity) for t in fills if day_of(t) < today
        )
        older = held_quantity - before_today
        if older > 0 and holding is not None:
            episode = f"before-{history_from.isoformat()}"
            book.lots.append(_Lot(older, holding.avg_cost, history_from, 0.0, episode))
            book.episode = episode

        for fill in fills:
            if fill.side is Side.BUY:
                if not book.lots:
                    book.episode = fill.trade_id
                book.lots.append(
                    _Lot(
                        fill.quantity,
                        fill.price,
                        day_of(fill),
                        fill.charges / fill.quantity if fill.quantity else 0.0,
                        book.episode or fill.trade_id,
                    )
                )
            else:
                unmatched = _sell(book, fill, day_of(fill))
                if unmatched:
                    mismatches.append(
                        f"{symbol}: sold {unmatched} more than the trades since "
                        f"{history_from:%d %b %Y} and holdings explain."
                    )

        open_quantity = sum(lot.quantity for lot in book.lots)
        expected = held_quantity + sum(
            (t.quantity if t.side is Side.BUY else -t.quantity) for t in fills if day_of(t) >= today
        )
        if open_quantity != expected and not (holding is None and open_quantity == 0):
            mismatches.append(
                f"{symbol}: trades leave {open_quantity} held, the broker shows {expected}."
            )

        positions.extend(_closed_rows(symbol, funding, isins.get(symbol), book))
        if book.lots:
            positions.append(_open_row(symbol, funding, isins.get(symbol), book, history_from))
    return Reconciled(tuple(positions), tuple(mismatches))


def _date_of(trade: BrokerTrade) -> dt.date:
    return trade.time.date()


def _sell(book: _Book, fill: BrokerTrade, day: dt.date) -> int:
    """Consume lots FIFO into the day's exits; returns the quantity left unmatched."""
    remaining = fill.quantity
    sell_charge = fill.charges / fill.quantity if fill.quantity else 0.0
    while remaining and book.lots:
        lot = book.lots[0]
        taken = min(lot.quantity, remaining)
        exit_ = book.exits.setdefault((day, lot.episode), _Exit(episode=lot.episode))
        exit_.quantity += taken
        exit_.cost += taken * lot.price
        exit_.proceeds += taken * fill.price
        exit_.charges += taken * (lot.charge_per_share + sell_charge)
        exit_.opened_on = lot.day if exit_.opened_on is None else min(exit_.opened_on, lot.day)
        lot.quantity -= taken
        remaining -= taken
        if lot.quantity == 0:
            book.lots.popleft()
    if not book.lots:
        book.episode = None
    return remaining


def _closed_rows(
    symbol: str, funding: str, isin: str | None, book: _Book
) -> list[ImportedPosition]:
    rows: list[ImportedPosition] = []
    for (day, _), exit_ in sorted(book.exits.items()):
        if not exit_.quantity or exit_.opened_on is None:
            continue
        rows.append(
            ImportedPosition(
                ref=f"{symbol}:{funding}:closed:{exit_.episode}:{day.isoformat()}",
                symbol=symbol,
                isin=isin,
                funding=funding,
                quantity=exit_.quantity,
                entry=exit_.cost / exit_.quantity,
                opened_on=exit_.opened_on,
                closed_on=day,
                exit_price=exit_.proceeds / exit_.quantity,
                charges=exit_.charges,
            )
        )
    return rows


def _open_row(
    symbol: str, funding: str, isin: str | None, book: _Book, history_from: dt.date
) -> ImportedPosition:
    quantity = sum(lot.quantity for lot in book.lots)
    cost = sum(lot.quantity * lot.price for lot in book.lots)
    first = book.lots[0]
    older = first.episode.startswith("before-")
    return ImportedPosition(
        ref=f"{symbol}:{funding}:open:{first.episode}",
        symbol=symbol,
        isin=isin,
        funding=funding,
        quantity=quantity,
        entry=cost / quantity,
        opened_on=min(lot.day for lot in book.lots),
        charges=sum(lot.quantity * lot.charge_per_share for lot in book.lots),
        note=f"held before {history_from:%d %b %Y} (at the holding's average cost)"
        if older
        else "",
    )
