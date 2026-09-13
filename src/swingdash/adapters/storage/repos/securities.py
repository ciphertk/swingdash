"""
NSE reference datasets. Each is replaced whole, in one transaction, so a
reader never sees half a refresh and a failed fetch leaves the previous copy
in place.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterable, Sequence

from swingdash.adapters.storage.db import Database
from swingdash.domain.securities import (
    BandEntry,
    DatasetStatus,
    Etf,
    IndexRow,
    ListedEquity,
    PriceBand,
    Surveillance,
)

LISTINGS = "listings"
BANDS = "bands"
SURVEILLANCE = "surveillance"
ETFS = "etfs"
INDICES = "indices"
DATASETS = (LISTINGS, BANDS, SURVEILLANCE, ETFS, INDICES)


class SecuritiesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- writes -------------------------------------------------------------

    def replace_listings(self, rows: Sequence[ListedEquity], status: DatasetStatus) -> None:
        self._replace(
            "ref_listings",
            ("symbol", "name", "isin", "listed_on"),
            ((r.symbol, r.name, r.isin, _iso(r.listed_on)) for r in rows),
            status,
        )

    def replace_bands(self, rows: Sequence[BandEntry], status: DatasetStatus) -> None:
        self._replace(
            "ref_bands",
            ("symbol", "name", "band"),
            ((r.symbol, r.name, r.band.value if r.band else None) for r in rows),
            status,
        )

    def replace_surveillance(self, rows: dict[str, Surveillance], status: DatasetStatus) -> None:
        self._replace(
            "ref_surveillance",
            ("symbol", "gsm", "esm", "ltasm", "stasm", "ibc"),
            ((symbol, s.gsm, s.esm, s.ltasm, s.stasm, s.ibc) for symbol, s in rows.items()),
            status,
        )

    def replace_etfs(self, rows: Sequence[Etf], status: DatasetStatus) -> None:
        self._replace(
            "ref_etfs",
            ("symbol", "name", "underlying", "asset_class", "isin", "listed_on"),
            (
                (r.symbol, r.name, r.underlying, r.asset_class, r.isin, _iso(r.listed_on))
                for r in rows
            ),
            status,
        )

    def replace_indices(self, rows: Sequence[IndexRow], status: DatasetStatus) -> None:
        self._replace(
            "ref_indices",
            (
                "name",
                "category",
                "last",
                "change_pct",
                "pe",
                "pb",
                "dividend_yield",
                "year_high",
                "year_low",
                "advances",
                "declines",
            ),
            (
                (
                    r.name,
                    r.category,
                    r.last,
                    r.change_pct,
                    r.pe,
                    r.pb,
                    r.dividend_yield,
                    r.year_high,
                    r.year_low,
                    r.advances,
                    r.declines,
                )
                for r in rows
            ),
            status,
        )

    def record_error(self, dataset: str, error: str) -> None:
        """Note a failed refresh without touching the data or its last good dates."""
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO ref_datasets (name, rows, error) VALUES (?, 0, ?)"
                " ON CONFLICT(name) DO UPDATE SET error = excluded.error",
                (dataset, error),
            )

    def _replace(
        self,
        table: str,
        columns: tuple[str, ...],
        values: Iterable[tuple[object, ...]],
        status: DatasetStatus,
    ) -> None:
        placeholders = ", ".join("?" * len(columns))
        with self._db.transaction() as conn:
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            _write_status(conn, status)

    # --- reads --------------------------------------------------------------

    def listings(self) -> list[ListedEquity]:
        return [
            ListedEquity(r["symbol"], r["name"], r["isin"], _date(r["listed_on"]))
            for r in self._rows("SELECT * FROM ref_listings")
        ]

    def bands(self) -> list[BandEntry]:
        return [
            BandEntry(r["symbol"], r["name"], PriceBand(r["band"]) if r["band"] else None)
            for r in self._rows("SELECT * FROM ref_bands ORDER BY symbol")
        ]

    def surveillance(self) -> dict[str, Surveillance]:
        return {
            r["symbol"]: Surveillance(r["gsm"], r["esm"], r["ltasm"], r["stasm"], r["ibc"])
            for r in self._rows("SELECT * FROM ref_surveillance")
        }

    def etfs(self) -> list[Etf]:
        return [
            Etf(
                r["symbol"],
                r["name"],
                r["underlying"],
                r["asset_class"],
                r["isin"],
                _date(r["listed_on"]),
            )
            for r in self._rows("SELECT * FROM ref_etfs ORDER BY symbol")
        ]

    def indices(self) -> list[IndexRow]:
        return [
            IndexRow(
                r["name"],
                r["category"],
                r["last"],
                r["change_pct"],
                r["pe"],
                r["pb"],
                r["dividend_yield"],
                r["year_high"],
                r["year_low"],
                r["advances"],
                r["declines"],
            )
            for r in self._rows("SELECT * FROM ref_indices ORDER BY category, name")
        ]

    def statuses(self) -> dict[str, DatasetStatus]:
        return {
            r["name"]: DatasetStatus(
                r["name"],
                _date(r["as_of"]),
                dt.datetime.fromisoformat(r["fetched_at"]) if r["fetched_at"] else None,
                r["rows"],
                r["error"],
            )
            for r in self._rows("SELECT * FROM ref_datasets")
        }

    def _rows(self, sql: str) -> list[sqlite3.Row]:
        return self._db.connection().execute(sql).fetchall()


def _write_status(conn: sqlite3.Connection, status: DatasetStatus) -> None:
    conn.execute(
        """
        INSERT INTO ref_datasets (name, as_of, fetched_at, rows, error) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET as_of = excluded.as_of,
            fetched_at = excluded.fetched_at, rows = excluded.rows, error = excluded.error
        """,
        (
            status.name,
            _iso(status.as_of),
            status.fetched_at.isoformat() if status.fetched_at else None,
            status.rows,
            status.error,
        ),
    )


def _iso(date: dt.date | None) -> str | None:
    return date.isoformat() if date else None


def _date(text: str | None) -> dt.date | None:
    return dt.date.fromisoformat(text) if text else None
