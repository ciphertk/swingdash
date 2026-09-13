# swingdash

Personal screener for swing trading in the NSE cash market, long-only. It
answers "which stocks deserve a closer look today" before a position is
opened - a pre-trade filter, not an order tool. Built on the Upstox
Analytics Token, delivered as a multi-tab Textual terminal dashboard. There
is no web frontend and no HTTP API; don't reintroduce one unless asked.

Tabs today: **Live RVOL**. Planned: Burst Score / Mswing (watchlist tabs),
FII/DII flows and market breadth/MBI (market-wide tabs, likely NSE-sourced).

## Commands

```powershell
uv sync                           # dev env; `uv run pre-commit install` once
uv run swingdash [-w NAME | -s A,B]   # dashboard (default command: run)
uv run swingdash setup | doctor [--feed] | replay [SYMS] | migrate-legacy --from PATH
uv run pytest                     # offline: unit, integration, ui (pilot)
uv run pytest -m network          # live Upstox checks
uv run ruff check --fix; uv run ruff format; uv run pyright; uv run lint-imports
```

Before calling a change done: all four checks plus pytest clean. For RVOL
maths changes also `swingdash replay` (works with the market closed). For
UI changes drive the app headlessly with `App.run_test()` (see
`tests/ui/test_dashboard.py` for the fake-services fixture).

## Layout and layering

```
src/swingdash/
  cli.py, bootstrap.py      entry + composition root (only place wiring every layer)
  settings.py               Settings/Paths (platformdirs, SWINGDASH_HOME override)
  logging_setup.py          rotating file log, token redaction, no stdout
  domain/                   PURE stdlib: bars, calendar, watchlist, rvol/{calc,types,curve}, metrics/*
  adapters/storage/         Database (per-thread conns), migrations, repos/*
  adapters/upstox/          the only home of upstox_client: client, feed, history, quotes, calendar, ...
  services/                 orchestration, threads, caches; ports.py = Protocols at test seams
    container.py            frozen Services DI container (built by bootstrap.build_services)
    market_data_hub.py      the ONE live feed, shared by every consumer
    rvol/                   engine, baselines, replay
  ui/                       the only place textual is imported
    app.py                  shell: header, tab strip, global watchlist, CRUD actions
    tabs/registry.py        TABS tuple - adding a tab = one package + one entry
    tabs/base.py            TabBase lifecycle (lazy mount, pause when hidden, error containment)
    tabs/rvol/              LiveRvolTab + table + tcss
    watchlist/, widgets/    header picker/modals, market badge, error panel
tests/  fakes/ unit/ integration/ ui/ network/
docs/pine/                  original TradingView sources the metrics were ported from
```

`ui -> services -> adapters -> domain` is enforced by import-linter
contracts in `pyproject.toml` (also: domain has no frameworks/SDKs/I/O,
services depend on `ports.py` not concrete Upstox adapters). If a contract
breaks, fix the design - don't loosen the contract.

User data never lives in the repo: DB/token/logs/instrument cache are under
the OS user dirs (`%LOCALAPPDATA%\swingdash`). Schema changes go in
`adapters/storage/migrations.py` as a new `PRAGMA user_version` step.

## Architecture principles

- **One feed for the whole app.** Upstox allows **2 feed connections per
  account**. Everything streams through `MarketDataHub.subscribe()`, which
  ref-counts keys (only 0<->1 transitions hit the socket), re-sends the set
  on reconnect and caps at 2000 instruments. Never open a feed elsewhere.
- **Pull, don't push.** Service threads never call Textual. Ticks write
  scalars only; tabs poll snapshots from a timer (`REFRESH_HZ`). Discrete
  events go through `events()`.
- **Global watchlist = symbols only.** `SwingDashApp.watchlist` is a frozen
  `Watchlist(name, symbols)`. Tabs opt in with `uses_watchlist = True`;
  market-wide tabs ignore it. Tabs never read each other - a metric needed
  by two tabs moves into a service (e.g. a future `RvolService` handing out
  leases), built when the second consumer actually appears.
- **Tabs:** subclass `TabBase`; implement `on_tab_mount`, `refresh_view`,
  `on_watchlist_changed`. Don't define `on_mount`. Tab keys must avoid the
  app's reserved `1-9 w n e d q ctrl+p`. Scope CSS in the tab's `.tcss`.
- **Domain is pure, services do I/O.** Metrics take data in and return a
  score; register them in `domain/metrics/registry.py`.
- Caching tiers stay separate: near-real-time (feed, delta-fetched candle
  cache) vs daily TTL (fundamentals). SQLite is enough for this scale.

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
- History V3: 1-minute candles cap at **1 month per call** (we use 28-day
  spans). Historical endpoints exclude today even after close.
  Rate limit 50/s, 500/min.
- Quotes: max 500 instrument_keys per call.
- Holidays: decide "closed" from `NSE in closed_exchanges`, not
  `holiday_type` - settlement holidays and special timings trade normally.
- Fundamentals `sector_market_cap_inr` is the company's own market cap
  despite the name.
- The `[WinError 6]` printed at exit is SDK teardown noise.

## RVOL (the metric in production)

Baseline `curve[m]` = average cumulative volume at minute-of-session `m`
over the last 20 sessions, built from 1-minute candles.

- `RVOL = vtt / curve[minute]` - time-of-day normalised; equals projected
  close RVOL. `RVOL-D = vtt / avg_full_day_volume` - Pine-equivalent.
- Verified: `vtt` matches summed 1-minute volume within 0.04% (no pre-open
  adjustment needed); a session is exactly 375 minutes, 09:15-15:29.
- Always compute against `active_session()` - the last session that has
  **opened** - never "today": weekends, holidays and pre-open show the
  previous close. The engine rolls over at the next open.
- Do not filter high-volume days out of the baseline (it biased RVOL ~12%).

## Ported, not yet in a tab

`domain/metrics/burst_score.py`, `mswing.py` (+ `ema.py`) are tested ports
of the Pine scripts; `services/fundamentals.py` (market cap, sector) and
`services/candles.py` (daily candle cache) back them. Still to build: cap
classification (SEBI rank-based), distance from 52-week high/low, sector
strength/rotation. Free float has no confirmed Upstox source.

## Conventions and gotchas

- Never read or print the token or `.env*` files (permission-denied on
  purpose). `doctor` shows only `****last4`.
- SQLite connections are per-thread (`Database.connection()`); a shared
  connection crashed under the baseline thread pool.
- Textual 8.x: private attrs on App/Widget must not shadow framework ones
  (`_log`, `_ready` bit us - check `dir(App)`); a focused Input swallows
  printable keys so bindings can't fire while typing; Input messages bubble
  up from modals (check `event.input.id`); `Select.BLANK` is `False` while
  blanks arrive as `NoSelection` (guard on type).
- Watchlist paste strips exchange prefixes (`NSE:RAYMOND` -> `RAYMOND`).
- Pyright is strict on `domain/`; SDK responses are typed `Any` on purpose.
