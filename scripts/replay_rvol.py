"""
Replay a past session minute-by-minute and prove the RVOL maths.

    python scripts/replay_rvol.py                    # default symbols
    python scripts/replay_rvol.py RELIANCE TCS

Works with the market closed, which is the point - correctness shouldn't
need a live session to verify.

Three assertions:
  1. curve[-1] == the average full-day volume over the same days (the
     identity the projected-close property depends on).
  2. At the final minute, intraday RVOL == day RVOL. They're defined
     differently but must converge, since curve[-1] IS the full-day
     average.
  3. Day RVOL matches the legacy engines/rvol.compute_rvol on daily bars,
     so the streaming path agrees with the original Pine port.
"""
import datetime as dt
import sys
from collections import defaultdict
from itertools import accumulate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import rvol as legacy_rvol
from app.live import baseline as bl
from app.live import rvol_calc
from app.live.session import IST, get_session
from app.services import ingestion_service
from app.services.instrument_service import find_instrument_key

DEFAULT_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK"]
TOLERANCE_PCT = 2.0


def _group_by_date(bars):
    grouped = defaultdict(list)
    for bar in bars:
        grouped[dt.datetime.fromisoformat(bar.ts).astimezone(IST).date()].append(bar)
    return grouped


def replay(symbol: str) -> bool:
    key = find_instrument_key(symbol)
    if not key:
        print(f"{symbol}: not in instrument cache")
        return False

    window_end = dt.date.today() - dt.timedelta(days=1)
    window_start = window_end - dt.timedelta(days=ingestion_service.MAX_MINUTE_HISTORY_DAYS - 1)
    bars = ingestion_service.fetch_minute_candles(key, window_start, window_end)
    by_date = _group_by_date(bars)
    if len(by_date) < 3:
        print(f"{symbol}: not enough history to replay")
        return False

    dates = sorted(by_date)
    target_date = dates[-1]                      # replay the most recent session
    baseline_dates = dates[:-1]                  # baseline from everything before it

    session = get_session(target_date)
    if session is None:
        print(f"{symbol}: {target_date} was not a trading day")
        return False

    prior_bars = [b for d in baseline_dates for b in by_date[d]]
    baseline = bl.baseline_from_bars(prior_bars, session)
    if baseline is None:
        print(f"{symbol}: could not build baseline")
        return False

    # --- 1. identity ------------------------------------------------------
    day_totals = [sum(b.volume for b in by_date[d]) for d in baseline_dates[-baseline.days_used:]]
    avg_daily = sum(day_totals) / len(day_totals)
    identity_diff = abs(baseline.avg_full_day_volume - avg_daily) / avg_daily * 100

    # --- replay the target session as cumulative vtt ----------------------
    per_minute = [0.0] * session.minutes
    for bar in by_date[target_date]:
        index = session.minute_of(dt.datetime.fromisoformat(bar.ts))
        if index is not None:
            per_minute[index] += bar.volume
    cumulative = list(accumulate(per_minute))

    last = session.minutes - 1
    final_intraday = rvol_calc.rvol_intraday(int(cumulative[last]), baseline, last)
    final_day = rvol_calc.rvol_day(int(cumulative[last]), baseline)

    # --- 3. cross-check against the legacy daily engine -------------------
    daily_bars = ingestion_service.fetch_daily_candles(
        key, from_date=window_start, to_date=target_date
    )
    legacy = legacy_rvol.compute_rvol(daily_bars, length=baseline.days_used)
    legacy_ratio = legacy.rvol_pct / 100 if legacy.rvol_pct else None
    legacy_diff = abs(final_day - legacy_ratio) / legacy_ratio * 100 if legacy_ratio else None

    print(f"\n=== {symbol}  replaying {target_date} ({baseline.days_used}d baseline) ===")
    print(f"  curve[-1]              {baseline.avg_full_day_volume:>14,.0f}")
    print(f"  avg daily volume       {avg_daily:>14,.0f}")
    print(f"  [1] identity diff      {identity_diff:>13.2f}%  {_verdict(identity_diff)}")
    print(f"  session volume         {cumulative[last]:>14,.0f}")
    print(f"  [2] intraday @close    {final_intraday:>13.4f}x")
    print(f"      day      @close    {final_day:>13.4f}x   converge: {_verdict(abs(final_intraday-final_day)*100)}")
    print(f"  [3] legacy engine      {legacy_ratio:>13.4f}x   diff {legacy_diff:.2f}%  {_verdict(legacy_diff)}")

    print("  intraday RVOL through the day:")
    for label, minute in [("09:45", 30), ("10:30", 75), ("12:00", 165), ("14:00", 285), ("close", last)]:
        ratio = rvol_calc.rvol_intraday(int(cumulative[minute]), baseline, minute)
        pct_done = cumulative[minute] / cumulative[last] * 100
        print(f"     {label:>5}  {ratio:6.2f}x   ({pct_done:5.1f}% of the day's volume done)")

    return (
        identity_diff < TOLERANCE_PCT
        and abs(final_intraday - final_day) < 1e-9
        and (legacy_diff is None or legacy_diff < TOLERANCE_PCT)
    )


def _verdict(diff_pct: float | None) -> str:
    if diff_pct is None:
        return "SKIP"
    return "PASS" if diff_pct < TOLERANCE_PCT else "FAIL"


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    results = [replay(s.upper()) for s in symbols]
    print(f"\n{sum(results)}/{len(results)} symbols passed all assertions")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
