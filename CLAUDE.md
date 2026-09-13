# swingdash

Personal screener for swing trading in the NSE cash market, long-only. It
answers "which stocks deserve a closer look today" before a position is
opened - a pre-trade filter, not an order tool. Built on the Upstox
Analytics Token, delivered as a multi-tab Textual terminal dashboard. There
is no web frontend and no HTTP API; don't reintroduce one unless asked.

Tabs today: **Live RVOL** (1, watchlist), **Securities** (2, market-wide:
every NSE EQ stock/index/ETF with price band, surveillance and sector),
**Scanner** (3, watchlist: Burst Power and Mswing - the home for further
per-symbol metrics) and **Chartink** (4, saved Chartink screeners and
dashboard widgets, with band and Burst Power added to stock lists).
Planned: FII/DII flows and market breadth/MBI (market-wide, likely
NSE-sourced like Securities).

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
  domain/                   PURE stdlib: bars, calendar, watchlist, securities, rvol/*, metrics/*
    scanner.py              per-symbol prepare (history) / live (price) split + the "today" rule
  adapters/storage/         Database (per-thread conns), migrations, repos/*
  adapters/upstox/          the only home of upstox_client: client, feed, history, quotes, calendar, ...
  adapters/nse/             public NSE archive files + report endpoints (no login, no upstox_client)
  adapters/chartink/        http (CSRF session, pacing), parsers (responses, pages), source
  services/                 orchestration, threads, caches; ports.py = Protocols at test seams
    container.py            frozen Services DI container (built by bootstrap.build_services)
    market_data_hub.py      the ONE live feed, shared by every consumer
    rvol/                   engine, baselines, replay
    securities.py           Securities tab's data: NSE datasets + Upstox sector backfill
    scanner/engine.py       Scanner's live engine: cached history -> prepare, deltas, O(1) snapshots
    candles.py              daily candle cache; calendar-aware (no call once it has the last session)
    daily_contexts.py       DailyContextLoader: cache-first prepare + paced delta fetches (Scanner, Chartink)
    chartink.py             saved items, sequential run queue, band/Burst enrichment
    preferences.py          remembered choices (Scanner benchmark index)
  ui/                       the only place textual is imported
    app.py                  shell: header, tab strip, global watchlist, CRUD actions
    export.py               ExportTable + write_csv - the `x` CSV export shared by every tab
    tradingview.py           chart_url/open_chart - the `o`/Enter chart-open shared by every tab
    tabs/registry.py        TABS tuple - adding a tab = one package + one entry
    tabs/base.py            TabBase lifecycle (lazy mount, pause when hidden, error containment)
    tabs/rvol/              LiveRvolTab + tcss (watchlist tab)
    tabs/securities/        SecuritiesTab + views.py (columns/sort/filter per view) + tcss (market-wide)
    tabs/scanner/           ScannerTab + columns.py + detail.py (panel) + index_picker.py (watchlist)
    tabs/chartink/          ChartinkTab (tree + results) + cells.py + add_modal + dashboard_picker
    watchlist/, widgets/    header picker/modals, market badge, error panel, NavTable, LiveTable
tests/  fakes/ fixtures/{nse,chartink}/ unit/ integration/ ui/ network/
docs/pine/                  original TradingView sources the metrics were ported from
```

`ui -> services -> adapters -> domain` is enforced by import-linter
contracts in `pyproject.toml` (also: domain has no frameworks/SDKs/I/O,
services depend on `ports.py`, not concrete Upstox/NSE/Chartink adapters). If a contract
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
- **Per-symbol metrics split into prepare and live** (`domain/scanner.py`).
  `prepare_symbol` walks completed daily history once per session
  (~1.4ms/symbol); `live_symbol` adds the current price in O(1) at every
  redraw (~2ms for 450 symbols). A tick only stores a price. Adding a
  Scanner metric: extend `SymbolContext`/`SymbolMetrics`, compute it in
  both functions (prove live == recompute-with-the-price-appended, as
  `tests/unit/test_mswing.py` does), add columns in
  `ui/tabs/scanner/columns.py` and panel lines in `detail.py`.
- **"Today" for daily metrics** follows RVOL's rule (active session, never
  the clock) and is only added when history reaches the previous session -
  so weekends, holidays and a stale cache never double count or skip a day.
  Mswing uses the live price as today's close at any time (TradingView's
  last bar); Burst Power counts today only once the session has closed.
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
  For live values over many rows use `ui/widgets/live_table.py` (in-place
  cell updates, reorder via `sort` at most every 2s, never while
  navigating); for static data see `ui/tabs/securities/pane.py`.
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
  spans); daily candles at **1 decade per call** (3 years = one call).
  Historical endpoints exclude today even after close.
- **Rate limits: documented 50/s, 500/min, 2000/30min per account - but
  Upstox's Cloudflare edge is stricter per IP.** Bursts near 40/s on the
  history endpoint drew 429 "Error 1015: You are being rate limited" for
  every request from the IP (lifted within ~15 minutes); ~10-12/s is fine.
  The edge also holds a throttled request ~20s before answering, and the
  SDK sets no timeout. So **every REST call goes through
  `UpstoxClient.call`**: shared `RateLimiter` (10/s, 250/min, 1500/30min),
  a `(10s, 45s)` timeout, and on 429 a 60s pause for all callers plus
  `RateLimitedError`, which callers retry rather than drop. Don't call SDK
  API methods directly; don't raise the limiter without re-verifying.
- Feed: index instruments arrive as `fullFeed.indexFF.ltpc` (ltp, cp, no
  vtt); equities as `marketFF`. Both reach consumers via the same tick.
- Quotes: max 500 instrument_keys per call.
- Holidays: decide "closed" from `NSE in closed_exchanges`, not
  `holiday_type` - settlement holidays and special timings trade normally.
- Fundamentals `sector_market_cap_inr` is the company's own market cap
  despite the name.
- Every SDK `ApiClient` starts a `ThreadPool` (a thread per CPU) just for
  `async_req` calls we never make, and closes it in `__del__` - which printed
  "Error during ApiClient cleanup: [WinError 6]" at exit on Windows.
  `adapters/upstox/client.py` swaps in `LazyThreadPool` on import (no threads
  unless async is used). Don't remove it, and keep all SDK access behind
  `UpstoxClient` so the swap applies.

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

## Chartink - verified facts (adapters/chartink, Chartink tab)

No public API: we replay the website's own requests, anonymously. Verified
13 Sep 2026 with read-only probes; `robots.txt` disallows nothing.

- **Session:** GET any page -> `<meta name="csrf-token">` plus cookies; POST
  with header `x-csrf-token` and a browser User-Agent. A stale token gives
  **419** `{"message":"CSRF token mismatch."}` - `ChartinkHttp` refetches the
  token once and retries. 403/429/503 mean Cloudflare/rate limiting.
- **Screener:** `POST /screener/process` form `scan_clause` (the site also
  sends `debug_clause`, optionally `column_clause` for custom columns) ->
  `{data:[{sr, nsecode, name, bsecode, close, per_chg, volume, ...}]}`.
  `nsecode` is empty for BSE-only rows.
- **Custom columns** (verified with the user's payload): with a
  `column_clause`, values come back as `scan-column-<id>` (built-ins:
  `default-close|percent-change|volume`) plus `<id>-conditional-filters-color`
  = 1-based index into that column's colour rules (last = "otherwise").
  The parser renames built-ins to `close/per_chg/volume`, keeps custom ones
  as `<id>`, and stores flags on the row under `color_key(column)`. Names and
  colours exist **only on the screener page** (`:scan-json.atlas_json` ->
  `columns.children[{id, name, colorFilters.children[{color}]}]`); Chartink's
  browser code compiles that tree into `column_clause`, so a link alone can't
  request custom columns. Don't re-implement that compiler - take the payload
  (link + payload in one paste, or `ColumnsPayloadModal` after a link import).
- **Widget:** `POST /widget/process` form `query` (`select ... GROUP BY ...`),
  `use_live=1`, `limit`, `size` -> `{metaData:[{columnAliases, groups,
  lastUpdateTime(ms), availableLimit}], groupData:[{name, results:[{alias:
  [values...]}]}]}`. The last value of each series is the latest; we force
  `size=1`. `limit=1000` is honoured. `groups` is `["symbol"]`, a
  sector/industry/marketcapname grouping, or empty (one `*no-groups*` row).
- **Pages:** a dashboard page embeds `:dashboard` (`id, name, is_private`)
  and `:widgets` (the dashboard's own; `jsondetails.resultType` = table /
  barchart / areachart). Ignore `:template-widgets` - Chartink's starter set
  on every page. A screener page embeds `:scan-json` whose `atlas_query` is
  the ready scan clause. Private ones don't render for anonymous users.
- **Data** is Chartink's free tier: delayed ~5 minutes.
- **Rules we keep:** on demand only (never poll), requests serialised and
  >= 1s apart, `(10, 30)` timeouts, no Chartink credentials or cookies
  stored. A layout change should fail loudly in the parsers
  (`ChartinkFormatError`), confined to the tab.

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

## Burst Power and Mswing (the Scanner)

Ports of `docs/pine/Burst Power.pine` and `Mswing Homma.pine`, on daily
candles (3 years, cached in `daily_candles`).

- Burst Power: close-over-close moves 5-10 / 10-19 / 19%+ since a calendar
  cutoff (today - 3 years; the bar before the cutoff supplies the first
  day's previous close, as Pine's `close[1]` does). `round(c5/5 + c10/2 +
  c19/0.5)`; dot green >=15, orange >=10. Pine's "closing within % of highs"
  filter is off by default and not exposed.
- Mswing: `momo(20) + momo(50)`, `momo(n) = (close - close[n]) * 100 /
  close[n] / n`, IPO-adjusted for short histories; classified against the
  benchmark index (default NIFTY MIDSML 400, user-changeable, stored in
  app_state). EMA(9) is SMA-seeded; Pine's seed may differ but converges
  over hundreds of bars.

## Not yet in a tab

Still to build: cap classification (SEBI rank-based), distance from 52-week
high/low, sector strength/rotation (the Securities tab's per-stock sector is
the input this would aggregate). Per-symbol ones belong in the Scanner via
the prepare/live split. Free float has no confirmed Upstox source.

## Conventions and gotchas

- Never read or print the token or `.env*` files (permission-denied on
  purpose). `doctor` shows only `****last4`.
- SQLite connections are per-thread (`Database.connection()`); a shared
  connection crashed under the baseline thread pool.
- Textual 8.x: private attrs on App/Widget must not shadow framework ones
  (`_log`, `_ready` bit us - check `dir(App)`); a focused Input swallows
  printable keys so bindings can't fire while typing; Input messages bubble
  up from modals (check `event.input.id`); `Select.BLANK` is `False` while
  blanks arrive as `NoSelection` (guard on type). `Tree.clear()` doesn't
  reset `cursor_node`: after rebuilding, read `tree.last_line` (lays out
  lines), then `move_cursor(None)` before `move_cursor(node)` - see
  `ChartinkTab._move_tree_cursor`. Tree labels given as `str` are markup;
  pass `Text` for user-supplied names.
- Watchlist paste strips exchange prefixes (`NSE:RAYMOND` -> `RAYMOND`).
- Pyright is strict on `domain/`; SDK responses are typed `Any` on purpose.
