"""
Ingestion service - the only module that talks to Upstox for market data.

Given instrument_keys, fetches:
  - historical daily candles (History API) - the rolling window RVOL,
    Burst Score, and Mswing all need
  - live quotes (Market Quote V3 API) - today's still-forming bar, since
    the historical endpoint only ever returns completed trading days
  - company profile (Fundamentals API) - market cap and sector, keyed by
    ISIN rather than instrument_key

Pure I/O, no metric math in here - see app/engines/ for that.
Callers (app/live, and any future dashboard metric) wire this output
into each engine's specific input.
"""
from __future__ import annotations

import datetime as dt
from typing import Sequence

from app.engines.types import DailyBar, MinuteBar
from app.upstox_client_wrapper import fundamentals_api, history_v3_api, market_quote_v3_api

# Upstox caps 1-15 minute historical data at a 1-month range per call, so
# ~20 trading days (about 28 calendar days) is one call per symbol. Going
# wider would silently need a second call.
MAX_MINUTE_HISTORY_DAYS = 28


def fetch_daily_candles(
    instrument_key: str,
    lookback_days: int | None = None,
    from_date: dt.date | None = None,
    to_date: dt.date | None = None,
) -> list[DailyBar]:
    """
    Completed daily candles for one instrument, oldest -> newest. Upstox's
    history endpoint never includes today's still-open bar - combine with
    fetch_live_quotes() for an "as of right now" read while market is open.

    Pass either `lookback_days` (counts back from `to_date`, default
    today) or an explicit `from_date`/`to_date` pair.
    """
    to_date = to_date or dt.date.today()
    if from_date is None:
        if lookback_days is None:
            raise ValueError("pass either lookback_days or from_date")
        from_date = to_date - dt.timedelta(days=lookback_days)

    response = history_v3_api().get_historical_candle_data1(
        instrument_key, "days", 1, to_date.isoformat(), from_date.isoformat()
    )
    candles = response.data.candles  # Upstox returns newest -> oldest

    return [
        DailyBar(
            date=row[0][:10],
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
        )
        for row in reversed(candles)
    ]


def fetch_minute_candles(
    instrument_key: str,
    from_date: dt.date,
    to_date: dt.date | None = None,
) -> list[MinuteBar]:
    """
    1-minute candles for one instrument, oldest -> newest, EXCLUDING today
    (same as the daily endpoint - verified: after the 2026-09-11 close the
    newest daily candle was still 09-10). Use fetch_todays_minute_candles()
    for the current session.

    The range must stay within MAX_MINUTE_HISTORY_DAYS or Upstox rejects
    the request rather than truncating it.
    """
    to_date = to_date or dt.date.today()
    span = (to_date - from_date).days
    if span > MAX_MINUTE_HISTORY_DAYS:
        raise ValueError(
            f"minute history range {span}d exceeds Upstox's {MAX_MINUTE_HISTORY_DAYS}d cap per call"
        )

    response = history_v3_api().get_historical_candle_data1(
        instrument_key, "minutes", 1, to_date.isoformat(), from_date.isoformat()
    )
    return _to_minute_bars(response.data.candles)


def fetch_todays_minute_candles(instrument_key: str) -> list[MinuteBar]:
    """
    1-minute candles for the CURRENT trading day, oldest -> newest. The
    historical endpoint excludes today, so today needs this separate
    intraday endpoint (no date arguments - it always means "today").
    """
    response = history_v3_api().get_intra_day_candle_data(instrument_key, "minutes", 1)
    return _to_minute_bars(response.data.candles)


def _to_minute_bars(candles: list) -> list[MinuteBar]:
    # Upstox returns newest -> oldest; engines and baselines want chronological.
    return [
        MinuteBar(ts=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
        for row in reversed(candles)
    ]


def fetch_daily_candles_bulk(
    instrument_keys: Sequence[str],
    lookback_days: int | None = None,
    from_date: dt.date | None = None,
    to_date: dt.date | None = None,
) -> dict[str, list[DailyBar]]:
    """
    History API has no batch endpoint (one instrument_key per call, unlike
    quotes) - this just loops fetch_daily_candles for a whole watchlist so
    callers don't have to.
    """
    return {
        key: fetch_daily_candles(
            key, lookback_days=lookback_days, from_date=from_date, to_date=to_date
        )
        for key in instrument_keys
    }


# Upstox's Full Market Quote V3 endpoint caps `instrument_key` at 500
# comma-separated values per call (confirmed against the official docs:
# https://upstox.com/developer/api-documentation/get-full-market-quote-v3).
MAX_QUOTE_INSTRUMENTS_PER_REQUEST = 500


def fetch_live_quotes(instrument_keys: Sequence[str]) -> dict[str, DailyBar]:
    """
    Today's still-forming bar for each instrument_key, as a DailyBar - so
    it can be appended straight onto fetch_daily_candles()'s output for an
    "as of right now" RVOL/Mswing read while the market is open.

    Batches keys into as few API calls as Upstox's 500-per-call cap
    allows (comma-separated `instrument_key` list), rather than one
    request per symbol. Keyed by instrument_key - Upstox's own response
    is keyed by "EXCHANGE:SYMBOL" instead, which is remapped here via
    each quote's `instrument_token` field so callers never need to know
    that quirk.
    """
    if not instrument_keys:
        return {}

    today = dt.date.today().isoformat()
    api = market_quote_v3_api()
    bars: dict[str, DailyBar] = {}

    for i in range(0, len(instrument_keys), MAX_QUOTE_INSTRUMENTS_PER_REQUEST):
        chunk = instrument_keys[i : i + MAX_QUOTE_INSTRUMENTS_PER_REQUEST]
        response = api.get_full_market_quote_v3(instrument_key=",".join(chunk))
        for quote in response.data.values():
            ohlc = quote.ohlc
            bars[quote.instrument_token] = DailyBar(
                date=today,
                open=ohlc.open,
                high=ohlc.high,
                low=ohlc.low,
                close=ohlc.close,
                volume=quote.volume,
            )

    return bars


def fetch_company_profile(isin: str) -> dict:
    """
    Company profile from Upstox's Fundamentals API (GET /fundamentals/{isin}/profile).
    `sector_market_cap_inr`, despite its name, is the company's own market
    cap, not the sector's total - confirmed by comparing two different
    companies in the same sector ("Bank": HDFCBANK and ICICIBANK) and
    getting two different, individually-plausible values rather than one
    shared sector-wide figure.
    """
    data = fundamentals_api().get_company_profile(isin).data
    return {
        "sector": data.sector,
        "market_cap_cr": data.sector_market_cap_inr.value if data.sector_market_cap_inr else None,
        "company_profile": data.company_profile,
    }
