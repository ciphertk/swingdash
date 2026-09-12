"""
NSE session calendar - the single source of truth for "minute 0".

Nothing else in the codebase should hardcode 09:15. Session bounds come
from Upstox's exchange-timings endpoint (which handles holidays and
special sessions like muhurat trading), cached one row per date in
SQLite so it costs at most one API call per day.

Verified empirically on 2026-09-11: a regular NSE session returns exactly
375 one-minute candles, first stamped 09:15:00+05:30 and last 15:29:00,
so minute-of-session runs 0..374 inclusive.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache

import upstox_client

from app.db import get_connection, transaction
from app.upstox_client_wrapper import get_api_client

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Fallback only - used when the timings API can't be reached. Real bounds
# always come from the API when available.
_REGULAR_OPEN = dt.time(9, 15)
_REGULAR_CLOSE = dt.time(15, 30)

_EXCHANGE = "NSE"


@dataclass(frozen=True)
class Holiday:
    date: dt.date
    holiday_type: str      # TRADING_HOLIDAY | SETTLEMENT_HOLIDAY | SPECIAL_TIMING
    description: str
    nse_closed: bool

    @property
    def label(self) -> str:
        return self.holiday_type.replace("_", " ").title()


@dataclass(frozen=True)
class Session:
    date: dt.date
    open_at: dt.datetime  # tz-aware, IST
    close_at: dt.datetime

    @property
    def minutes(self) -> int:
        """Number of 1-minute buckets in this session (375 for a regular day)."""
        return int((self.close_at - self.open_at).total_seconds() // 60)

    def minute_of(self, moment: dt.datetime) -> int | None:
        """
        Index of `moment` within the session, 0-based. None if outside the
        session. Clamped to the last bucket so a tick arriving during the
        closing call doesn't overflow the baseline curve.
        """
        moment = moment.astimezone(IST)
        if moment < self.open_at:
            return None
        index = int((moment - self.open_at).total_seconds() // 60)
        if index >= self.minutes:
            return self.minutes - 1
        return index


def today_ist() -> dt.date:
    return dt.datetime.now(IST).date()


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def get_session(date: dt.date | None = None) -> Session | None:
    """
    Session bounds for `date`, or None if it wasn't a trading day.
    Cached in SQLite, including the "not a trading day" answer for past
    dates, so a weekend lookup doesn't re-hit the API every time.
    """
    date = date or today_ist()

    found, cached = _read_cached(date)
    if found:
        return cached

    try:
        bounds = _fetch_bounds(date)
    except Exception:
        # Network/API trouble shouldn't take the engine down - assume
        # regular hours on a weekday, closed at the weekend, and don't
        # cache, so it retries next time.
        return _regular_session(date) if date.weekday() < 5 else None

    # A trading day is final once published. "Not a trading day" is only
    # persisted for past dates, so a special session announced for today
    # can't be masked by an answer cached earlier in the day.
    if bounds is not None or date < today_ist():
        _write_cached(date, bounds)
    if bounds is None:
        return None
    open_at, close_at = bounds
    return Session(date=date, open_at=open_at, close_at=close_at)


# Longest NSE closure stretch in practice is a few days (e.g. a holiday
# abutting a weekend); this is a generous bound, not a tuning knob.
_MAX_LOOKBACK_DAYS = 10


def active_session(now: dt.datetime | None = None) -> Session | None:
    """
    The most recent session that has already OPENED - today's once it's
    past the open, otherwise the previous trading day's.

    This, not "today's session", is what RVOL must be computed against.
    On a weekend or holiday there is no session today, but the feed still
    serves the last session's closing volume; and before 09:15 on a trading
    day, the numbers on screen are still yesterday's. Anchoring to today in
    either case left RVOL blank.
    """
    now = now or now_ist()
    day = now.date()
    for _ in range(_MAX_LOOKBACK_DAYS):
        session = get_session(day)
        if session is not None and session.open_at <= now:
            return session
        day -= dt.timedelta(days=1)
    return None


@lru_cache(maxsize=1)
def _holidays_this_year() -> dict[dt.date, Holiday]:
    """
    One call per process. NSE being in `closed_exchanges` is what decides
    whether trading is actually off - `holiday_type` alone is misleading,
    since a SETTLEMENT_HOLIDAY or SPECIAL_TIMING day usually lists NSE
    under `open_exchanges` and trades normally.
    """
    try:
        api = upstox_client.MarketHolidaysAndTimingsApi(get_api_client())
        response = api.get_holidays()
    except Exception:
        return {}

    holidays: dict[dt.date, Holiday] = {}
    for entry in response.data or []:
        raw_date = getattr(entry, "_date", None)
        if raw_date is None:
            continue
        day = raw_date.date() if isinstance(raw_date, dt.datetime) else raw_date
        holidays[day] = Holiday(
            date=day,
            holiday_type=entry.holiday_type or "",
            description=entry.description or "",
            nse_closed=_EXCHANGE in (entry.closed_exchanges or []),
        )
    return holidays


def holiday_for(date: dt.date | None = None) -> Holiday | None:
    """The holiday entry for `date`, or None on an ordinary day."""
    return _holidays_this_year().get(date or today_ist())


def _fetch_bounds(date: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
    api = upstox_client.MarketHolidaysAndTimingsApi(get_api_client())
    response = api.get_exchange_timings(date.isoformat())
    for entry in response.data or []:
        if entry.exchange == _EXCHANGE:
            return (
                dt.datetime.fromtimestamp(int(entry.start_time) / 1000, IST),
                dt.datetime.fromtimestamp(int(entry.end_time) / 1000, IST),
            )
    return None  # exchange absent => not a trading day


def _regular_session(date: dt.date) -> Session:
    return Session(
        date=date,
        open_at=dt.datetime.combine(date, _REGULAR_OPEN, tzinfo=IST),
        close_at=dt.datetime.combine(date, _REGULAR_CLOSE, tzinfo=IST),
    )


def _read_cached(date: dt.date) -> tuple[bool, Session | None]:
    """
    Returns (found, session). The flag matters: a cached non-trading day
    and a date never looked up both have no Session, and conflating them
    meant non-trading days were never actually served from cache.
    """
    row = get_connection().execute(
        "SELECT open_ms, close_ms, is_trading_day FROM market_sessions WHERE date = ?",
        (date.isoformat(),),
    ).fetchone()
    if row is None:
        return False, None
    if not row["is_trading_day"]:
        return True, None
    return True, Session(
        date=date,
        open_at=dt.datetime.fromtimestamp(row["open_ms"] / 1000, IST),
        close_at=dt.datetime.fromtimestamp(row["close_ms"] / 1000, IST),
    )


def _write_cached(date: dt.date, bounds: tuple[dt.datetime, dt.datetime] | None) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO market_sessions (date, open_ms, close_ms, is_trading_day)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                open_ms = excluded.open_ms, close_ms = excluded.close_ms,
                is_trading_day = excluded.is_trading_day
            """,
            (
                date.isoformat(),
                int(bounds[0].timestamp() * 1000) if bounds else None,
                int(bounds[1].timestamp() * 1000) if bounds else None,
                1 if bounds else 0,
            ),
        )
