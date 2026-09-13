"""
Local SQLite cache for completed daily candles.

Why this exists: a completed trading day's candle never changes, but
re-fetching the full multi-year lookback window from Upstox on every
request was most of where the original REST dashboard's load time went. This caches what's already been fetched and
only asks Upstox for candles newer than the last cached date, per
CLAUDE.md's caching-tier guidance (cache what's static, keep only the
genuinely live pieces - today's still-forming bar via
ingestion_service.fetch_live_quotes - near-real-time and uncached).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from swingdash.db import get_connection, transaction
from swingdash.engines.types import DailyBar
from swingdash.services import ingestion_service

# History fetches are one network round trip per symbol (no batch
# endpoint - see ingestion_service). Running them concurrently is what
# keeps a cold cache or a large watchlist from paying for that
# sequentially; capped well under Upstox's 50 req/sec standard-API limit.
MAX_CONCURRENT_HISTORY_FETCHES = 10


def _latest_cached_date(instrument_key: str) -> dt.date | None:
    row = (
        get_connection()
        .execute(
            "SELECT MAX(date) AS latest FROM daily_candles WHERE instrument_key = ?",
            (instrument_key,),
        )
        .fetchone()
    )
    return dt.date.fromisoformat(row["latest"]) if row and row["latest"] else None


def _read_cached_bars(instrument_key: str, from_date: dt.date, to_date: dt.date) -> list[DailyBar]:
    rows = (
        get_connection()
        .execute(
            """
        SELECT date, open, high, low, close, volume FROM daily_candles
        WHERE instrument_key = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
        """,
            (instrument_key, from_date.isoformat(), to_date.isoformat()),
        )
        .fetchall()
    )
    return [
        DailyBar(row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"])
        for row in rows
    ]


def _upsert_bars(instrument_key: str, bars: list[DailyBar]) -> None:
    if not bars:
        return
    with transaction() as conn:
        conn.executemany(
            """
            INSERT INTO daily_candles (instrument_key, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_key, date) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume
            """,
            [(instrument_key, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def fetch_daily_candles_cached(instrument_key: str, lookback_days: int) -> list[DailyBar]:
    """
    Same contract as ingestion_service.fetch_daily_candles, but backed by
    the local cache: fetches from Upstox only the candles it doesn't
    already have. On a warm cache that's usually a 1-2 day delta (or
    nothing at all) instead of the full lookback window.

    Note: on a weekend/holiday this still makes one small delta-check
    call to Upstox (which comes back empty) rather than knowing in
    advance there's no new trading day - a NSE holiday calendar would
    avoid that, but isn't worth the complexity for one extra lightweight
    call per symbol per day at this scale.
    """
    today = dt.date.today()
    from_date = today - dt.timedelta(days=lookback_days)

    latest_cached = _latest_cached_date(instrument_key)

    if latest_cached is None:
        bars = ingestion_service.fetch_daily_candles(
            instrument_key, from_date=from_date, to_date=today
        )
        _upsert_bars(instrument_key, bars)
        return bars

    # Upstox's history endpoint never returns today's still-open bar, so
    # having yesterday's candle already means the cache is fully warm.
    yesterday = today - dt.timedelta(days=1)
    if latest_cached < yesterday:
        delta_from = latest_cached + dt.timedelta(days=1)
        new_bars = ingestion_service.fetch_daily_candles(
            instrument_key, from_date=delta_from, to_date=today
        )
        _upsert_bars(instrument_key, new_bars)

    return _read_cached_bars(instrument_key, from_date, today)


def fetch_daily_candles_cached_bulk(
    instrument_keys: Sequence[str], lookback_days: int
) -> dict[str, list[DailyBar]]:
    """
    Cached, concurrent version of ingestion_service.fetch_daily_candles_bulk.
    Each symbol's fetch_daily_candles_cached call gets its own Upstox
    ApiClient (see upstox_client_wrapper) and its own SQLite connection
    (app.db.get_connection is one-per-thread) - no state is shared across
    threads, so no locking is needed here.
    """
    if not instrument_keys:
        return {}

    results: dict[str, list[DailyBar]] = {}
    with ThreadPoolExecutor(
        max_workers=min(MAX_CONCURRENT_HISTORY_FETCHES, len(instrument_keys))
    ) as pool:
        futures = {
            pool.submit(fetch_daily_candles_cached, key, lookback_days): key
            for key in instrument_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()

    return results
