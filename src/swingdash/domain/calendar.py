"""
NSE trading calendar model - sessions, holidays, and which session is "live".

Verified empirically on 2026-09-11: a regular NSE session returns exactly
375 one-minute candles, first stamped 09:15:00+05:30 and last 15:29:00,
so minute-of-session runs 0..374 inclusive.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Only used as a fallback when real session bounds can't be fetched.
REGULAR_OPEN = dt.time(9, 15)
REGULAR_CLOSE = dt.time(15, 30)

# Longest NSE closure stretch in practice is a few days (e.g. a holiday
# abutting a weekend); this is a generous bound, not a tuning knob.
MAX_LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class Holiday:
    date: dt.date
    holiday_type: str  # TRADING_HOLIDAY | SETTLEMENT_HOLIDAY | SPECIAL_TIMING
    description: str
    # Decides whether trading is actually off. holiday_type alone is
    # misleading: SETTLEMENT_HOLIDAY and SPECIAL_TIMING days trade normally.
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
        Index of `moment` within the session, 0-based. None before the open.
        Clamped to the last bucket so a tick arriving during the closing call
        doesn't overflow the baseline curve.
        """
        moment = moment.astimezone(IST)
        if moment < self.open_at:
            return None
        index = int((moment - self.open_at).total_seconds() // 60)
        if index >= self.minutes:
            return self.minutes - 1
        return index


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def regular_session(date: dt.date) -> Session:
    return Session(
        date=date,
        open_at=dt.datetime.combine(date, REGULAR_OPEN, tzinfo=IST),
        close_at=dt.datetime.combine(date, REGULAR_CLOSE, tzinfo=IST),
    )


def active_session(
    now: dt.datetime, session_for: Callable[[dt.date], Session | None]
) -> Session | None:
    """
    The most recent session that has already OPENED - today's once it's
    past the open, otherwise the previous trading day's.

    This, not "today's session", is what RVOL must be computed against. On a
    weekend or holiday there is no session today, but the feed still serves
    the last session's closing volume; and before 09:15 on a trading day the
    numbers on screen are still yesterday's.
    """
    day = now.date()
    for _ in range(MAX_LOOKBACK_DAYS):
        session = session_for(day)
        if session is not None and session.open_at <= now:
            return session
        day -= dt.timedelta(days=1)
    return None


# NSE's end-of-day files for the next session (price bands ~19:50,
# surveillance ~21:00 IST, observed Sep 2026) are all out by then.
EOD_PUBLISH_TIME = dt.time(21, 30)


def latest_publish_cutoff(
    now: dt.datetime,
    session_for: Callable[[dt.date], Session | None],
    at: dt.time = EOD_PUBLISH_TIME,
) -> dt.datetime | None:
    """
    The most recent moment, at or before `now`, by which a trading day's
    end-of-day files should have been published. Data fetched before it is
    probably out of date.
    """
    day = now.date()
    for _ in range(MAX_LOOKBACK_DAYS):
        cutoff = dt.datetime.combine(day, at, tzinfo=IST)
        if cutoff <= now and session_for(day) is not None:
            return cutoff
        day -= dt.timedelta(days=1)
    return None
