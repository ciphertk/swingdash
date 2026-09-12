# Swing dashboard (terminal UI)

Personal screener for swing trading in the NSE cash market, long-only. It
answers "which stocks deserve a closer look today" before a position is
opened - a pre-trade filter, not an order tool. Built on the Upstox
Analytics Token, delivered as a Textual terminal UI. There is no web
frontend and no HTTP API; don't reintroduce one unless asked.

Today it shows **live RVOL** for a watchlist. Further dashboard metrics
will be added to the TUI.

## Run

```powershell
python -m venv .venv; .venv\Scripts\activate; pip install -r requirements.txt
copy .env.example .env               # paste UPSTOX_ANALYTICS_TOKEN
python scripts/test_connection.py    # once: verifies token, caches instrument masters
python scripts/rvol.py [watchlist] | -s SYM,SYM
```

Keys: `w` watchlist · `n` new · `e` edit · `d` delete · `s`/`r` sort ·
`/` filter · `f` freeze order · arrows/PgUp/PgDn/Home/End/`g`/`G` · `q`.

## Layout

```
app/
  config.py, db.py, upstox_client_wrapper.py   env/paths, SQLite, all SDK auth
  live/      streaming engine - session.py, baseline.py, rvol_calc.py, feed.py, engine.py
  tui/       Textual UI - the only package importing textual
  services/  I/O: ingestion, instruments, watchlists, candle cache, fundamentals
  engines/   pure metric functions (data in, score out)
scripts/     rvol.py (entry), test_connection.py, test_feed.py, replay_rvol.py
data/        instrument caches + swing_dashboard.db (gitignored; watchlist.json is the seed)
pineScripts/ original TradingView sources the metrics were ported from
```

## Architecture principles

- **Engines are pure, services do I/O.** Nothing in `engines/` or
  `live/rvol_calc.py` calls Upstox or touches the DB.
- **One engine process, UI attaches to it.** `live/` imports no UI code and
  no asyncio. Upstox allows only **2 feed connections per account**, so any
  second UI must share the engine rather than open its own feed.
- **Pull, don't push.** Ticks write scalars only; all maths happens in
  `RvolEngine.snapshot()`, which the TUI polls ~8Hz. Discrete events go
  through `events()`.
- **Engine registry, not hardcoded list** (`engines/registry.py`): adding a
  metric should be one engine file plus one registry entry.
- **Scalability notes / caching tiers.** Personal single-user tool: SQLite is
  enough (it replaced flat JSON once there was more than one watchlist).
  Keep tiers separate - near-real-time (feed, delta-fetched candle cache) vs
  daily TTL (fundamentals). The ~2,600-symbol instrument list is small
  enough to filter in memory.

## Upstox - verified facts

Verify anything new against the official docs rather than assuming
(append `.md` to a doc URL for clean markdown, e.g.
`https://upstox.com/developer/api-documentation/api-overview.md`).

- Analytics Token works for Market Quote, History, Fundamentals, market
  timings/holidays **and the WebSocket feed**, with no static IP. Never use
  Account/Portfolio/Order APIs (they need static IP, UDAPI1221).
- Feed: `MarketDataStreamerV3`, mode `full` is the only one carrying `vtt`
  (volume traded today). `connect()` is non-blocking. Also pushes a
  `market_info` message with `segmentStatus.NSE_EQ`. Limit 2000 instruments.
- History V3: 1-minute candles cap at **1 month per call** (~20 trading
  days = one call). Historical endpoints exclude today even after close.
  Rate limit 50/s, 500/min.
- Quotes: max 500 instrument_keys per call.
- Holidays: decide "closed" from `NSE in closed_exchanges`, not
  `holiday_type` - settlement holidays and special timings trade normally.
- Fundamentals `sector_market_cap_inr` is the company's own market cap
  despite the name.

## RVOL (the metric in production)

Baseline `curve[m]` = average cumulative volume at minute-of-session `m`
over the last 20 sessions, built from 1-minute candles.

- `RVOL = vtt / curve[minute]` - time-of-day normalised; equals projected
  close RVOL. `RVOL-D = vtt / avg_full_day_volume` - Pine-equivalent.
- Verified: `vtt` matches summed 1-minute volume within 0.04% (no pre-open
  adjustment needed); a session is exactly 375 minutes, 09:15-15:29.
- Always compute against `session.active_session()` - the last session that
  has **opened** - never "today": weekends, holidays and pre-open show the
  previous close. The engine rolls over at the next open.
- Do not filter high-volume days out of the baseline (it biased RVOL ~12%).

## Glossary - ported, not yet in the TUI

`engines/burst_score.py`, `engines/mswing.py` (+ `ema.py`) are tested ports
of the Pine scripts; `services/fundamentals_service.py` (market cap,
sector) and `services/candle_cache_service.py` (daily candles) back them.
Still to build: cap classification (SEBI rank-based), distance from 52-week
high/low, standalone EMA, sector strength/rotation. Free float has no
confirmed Upstox source.

## Conventions and gotchas

- Verify changes: `python scripts/replay_rvol.py` (correctness, works with
  market closed) and drive the TUI headlessly with `App.run_test()`.
- SQLite connections are per-thread (`db.get_connection`); a shared
  connection crashed under the baseline thread pool.
- Textual: Input messages bubble up from modals (check `event.input.id`);
  `query_one` searches the active screen (cache widget refs); `Select.BLANK`
  is `False` in 8.x while blanks arrive as `NoSelection` (guard on type).
- Watchlist paste strips exchange prefixes (`NSE:RAYMOND` -> `RAYMOND`).
