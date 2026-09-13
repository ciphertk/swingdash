# swingdash

Personal screener for swing trading in the NSE cash market, long-only. It
answers "which stocks deserve a closer look today" before a position is
opened - a pre-trade filter, not an order tool. Built on the Upstox
Analytics Token, delivered as a multi-tab Textual terminal dashboard. There
is no web frontend and no HTTP API; don't reintroduce one unless asked.

Tabs today: **Live RVOL** (watchlist tab) and **Securities** (market-wide:
every NSE EQ stock/index/ETF with price band, surveillance and sector).
Planned: Burst Score / Mswing (watchlist tabs), FII/DII flows and market
breadth/MBI (market-wide, likely NSE-sourced like Securities).

## Commands

```powershell
uv sync                           # dev env; `uv run pre-commit install` once
uv run swingdash [-w NAME | -s A,B]   # dashboard (default command: run)
uv run swingdash setup | doctor [--feed] | refresh [--no-sectors] | replay [SYMS] | migrate-legacy --from PATH
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
  domain/                   PURE stdlib: bars, calendar, watchlist, securities, rvol/{calc,types,curve}, metrics/*
  adapters/storage/         Database (per-thread conns), migrations, repos/*
  adapters/upstox/          the only home of upstox_client: client, feed, history, quotes, calendar, ...
  adapters/nse/             public NSE archive files + report endpoints (no login, no upstox_client)
  services/                 orchestration, threads, caches; ports.py = Protocols at test seams
    container.py            frozen Services DI container (built by bootstrap.build_services)
    market_data_hub.py      the ONE live feed, shared by every consumer
    rvol/                   engine, baselines, replay
    securities.py           Securities tab's data: NSE datasets + Upstox sector backfill
  ui/                       the only place textual is imported
    app.py                  shell: header, tab strip, global watchlist, CRUD actions
    export.py               ExportTable + write_csv - the `x` CSV export shared by every tab
    tradingview.py           chart_url/open_chart - the `o`/Enter chart-open shared by every tab
    tabs/registry.py        TABS tuple - adding a tab = one package + one entry
    tabs/base.py            TabBase lifecycle (lazy mount, pause when hidden, error containment)
    tabs/rvol/              LiveRvolTab + tcss (watchlist tab)
    tabs/securities/        SecuritiesTab + views.py (columns/sort/filter per view) + tcss (market-wide)
    watchlist/, widgets/    header picker/modals, market badge, error panel, shared NavTable
tests/  fakes/ fixtures/nse/ unit/ integration/ ui/ network/
docs/pine/                  original TradingView sources the metrics were ported from
```

`ui -> services -> adapters -> domain` is enforced by import-linter
contracts in `pyproject.toml` (also: domain has no frameworks/SDKs/I/O,
services depend on `ports.py` not concrete Upstox adapters). If a contract
breaks, fix the design - don't loosen the contract.

User data never lives in the repo: DB/token/logs/instrument cache/CSV
exports are under the OS user dirs (`%LOCALAPPDATA%\swingdash`, exports in
its `Exports` subfolder). Schema changes go in
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
  app's reserved `1-9 w n e d q ctrl+p`, plus `x` and `o` (CSV export and
  chart-open, bound once on `TabBase` - see below), and `enter` if the tab
  uses a `DataTable` (its own `RowSelected`/`select_cursor` binding also
  opens a chart via `TabBase`). Scope CSS in the tab's `.tcss`.
- **Domain is pure, services do I/O.** Metrics take data in and return a
  score; register them in `domain/metrics/registry.py`.
- Caching tiers stay separate: near-real-time (feed, delta-fetched candle
  cache), daily TTL (fundamentals), manual-only (Securities tab reference
  data - EOD, so refreshed on request, never polled). SQLite is enough for
  this scale.
- **End-of-day reference data never auto-refreshes.** `SecuritiesService`
  fetches only when asked (`R` in the tab, or `swingdash refresh`), runs on
  one background thread, is single-flight and cancellable, and rebuilds its
  snapshot from whatever is in the DB - so a dataset that fails to fetch
  keeps its last good copy rather than going blank.
- **DataTable at a few thousand rows is measurement-bound, not add-bound.**
  Adding rows re-measures every cell (~0.5s for ~2,300 rows); reordering
  (`table.sort`) and single-cell updates are ~30ms. Rebuild only when the
  visible row *set* changes (filter, new data); re-sort in place otherwise.
  See `ui/tabs/securities/pane.py` for the pattern if another tab grows a
  large table.
- **CSV export (`x`) is one mechanism, `ui/export.py`, shared by every tab.**
  `TabBase.action_export_csv` calls the tab's `export_data() -> ExportTable
  | None` and writes it; a tab only supplies its current rows, already
  filtered and sorted exactly as shown (never the full underlying dataset,
  never re-deriving order from scratch - reuse the same rows/keys the
  on-screen render used). Values are raw (numbers, ISO dates, joined label
  strings), never the styled `Text` cells the table renders - see
  `views.py`'s `Column.value` vs `Column.sort`/`View.cells`.
- **Opening a chart (`o`, or Enter/click-again on a row) is the same pattern,
  `ui/tradingview.py`.** `TabBase.action_open_chart` calls the tab's
  `chart_symbol() -> str | None` (the NSE symbol under the cursor) and shells
  out to the OS default browser; `TabBase.on_data_table_row_selected` calls
  the same action, which is how Enter and a second click on the
  already-selected row (DataTable's own "select" gesture) also open it - no
  extra wiring needed as long as the tab's table has `cursor_type="row"`.
  Return `None` (Securities' Indices view does) where a row's key isn't a
  real tradable symbol, rather than guessing a chart link that's wrong.
  Never call `webbrowser.open` directly from a tab - go through this so
  tests can intercept it (see `tests/ui/conftest.py`'s `opened_urls`).

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

## NSE - verified facts (adapters/nse, Securities tab)

Public files/endpoints, no login needed, but a browser User-Agent is
required or the CDN 403s. Verified Sep 2026:

- Stock universe for the tab is decided by **`sec_list.csv`'s EQ rows**, not
  `EQUITY_L.csv`'s SERIES column - the latter lags a day behind a stock
  that just moved BE<->EQ. `EQUITY_L.csv` is used only for name/ISIN/listing
  date lookups. ETFs also trade as EQ, so they're split out via
  `eq_etfseclist.csv`.
- Price bands (`sec_list.csv`, ~19:50 IST) and surveillance (report
  endpoints, ~21:00 IST) describe the **next** session; a
  `latest_publish_cutoff` of 21:30 IST drives the tab's "newer files likely"
  hint - it's an EOD-timing check, not a market-open check.
- Surveillance report endpoints (`api/reportASM|GSM|ESM`) are preferred
  over the daily `REG1_IND{DDMMYY}.csv` archive file, which is a day behind;
  the archive file is the fallback if a report call fails. `100` in the
  archive file means "not applicable", not zero.
- `allIndices` categories and 52w high/low sometimes come back as `0`
  meaning "not applicable" (e.g. fixed-income indices have no P/E) - treat
  0 as `None`, not a real value.

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
of the Pine scripts; `services/fundamentals.py` (market cap, sector, plus
the paced `backfill()` the Securities tab uses) and `services/candles.py`
(daily candle cache) back them. Still to build: cap classification (SEBI
rank-based), distance from 52-week high/low, sector strength/rotation
(the Securities tab's per-stock sector is the input this would aggregate).
Free float has no confirmed Upstox source.

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
