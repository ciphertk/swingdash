"""Fills imported from a broker, kept so positions can be rebuilt without refetching."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from swingdash.adapters.storage.db import Database
from swingdash.domain.broker import BrokerTrade, Side


class BrokerTradeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, broker: str, trades: Iterable[BrokerTrade]) -> int:
        """Insert or refresh by trade id (a day's trade book is re-read until it's history)."""
        rows = [
            (
                broker,
                t.trade_id,
                t.symbol,
                t.isin,
                t.side.value,
                t.product,
                t.quantity,
                t.price,
                t.time.isoformat(),
                t.charges,
            )
            for t in trades
        ]
        if not rows:
            return 0
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO broker_trades
                    (broker, trade_id, symbol, isin, side, product, quantity, price, traded_at, charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(broker, trade_id) DO UPDATE SET
                    symbol = CASE WHEN excluded.symbol = '' THEN broker_trades.symbol
                                  ELSE excluded.symbol END,
                    isin = COALESCE(excluded.isin, broker_trades.isin),
                    side = excluded.side,
                    product = excluded.product,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    traded_at = excluded.traded_at,
                    charges = MAX(excluded.charges, broker_trades.charges)
                """,
                rows,
            )
        return len(rows)

    def all(self, broker: str) -> list[BrokerTrade]:
        rows = (
            self._db.connection()
            .execute(
                "SELECT * FROM broker_trades WHERE broker = ? ORDER BY traded_at, trade_id",
                (broker,),
            )
            .fetchall()
        )
        return [
            BrokerTrade(
                trade_id=row["trade_id"],
                symbol=row["symbol"],
                isin=row["isin"],
                side=Side(row["side"]),
                product=row["product"],
                quantity=row["quantity"],
                price=row["price"],
                time=dt.datetime.fromisoformat(row["traded_at"]),
                charges=row["charges"],
            )
            for row in rows
        ]

    def count(self, broker: str) -> int:
        row = (
            self._db.connection()
            .execute("SELECT COUNT(*) FROM broker_trades WHERE broker = ?", (broker,))
            .fetchone()
        )
        return int(row[0])
