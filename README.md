# swingdash

A terminal dashboard for NSE swing trading, streaming live data from
Upstox. It opens as a set of tabs sharing one global watchlist and one live
feed.

- **Live RVOL** - time-of-day normalised, so 10:30 is compared against a
  typical 10:30 rather than a whole day.
- **Securities** - every NSE EQ stock, index and ETF, with price band and
  exchange surveillance flags, refreshed on request.
- **Scanner** - Burst Power and Mswing for every symbol in the watchlist,
  ports of the TradingView indicators, with Mswing moving live.
- **Chartink** - your Chartink screeners and dashboard widgets, saved and
  run with one key, with price band and Burst Power added to stock lists.
- **Risk** - how many shares to buy for a given stop and risk, charges
  included, and the open risk (portfolio heat) of the positions you hold.

## Install

Requires [uv](https://docs.astral.sh/uv/) and an Upstox **Analytics Token**
(account.upstox.com -> Developer Apps -> your app -> Analytics tab).

```powershell
uv tool install .      # puts `swingdash` on PATH
swingdash setup        # asks for the token, creates the database, downloads instruments
swingdash doctor       # confirms everything is in place
```

The token, database, caches and logs live in your OS user folders
(`%LOCALAPPDATA%\swingdash` on Windows), never in the repo. Set
`SWINGDASH_HOME` to keep everything under one folder instead.

## Run

```powershell
swingdash                       # last active watchlist
swingdash -w nxtDay             # a saved watchlist
swingdash -s RELIANCE,TCS       # ad-hoc symbols, not saved
```

| Key | Action |
|---|---|
| `1`-`9` | switch tab |
| `w` | pick a watchlist |
| `n` / `e` / `d` | new / edit / delete watchlist (paste symbols; `NSE:` prefixes are fine) |
| `ctrl+p` | command palette (watchlists, tabs, actions) |
| `q` | quit |

Every tab (each of Securities' views included) also has:

| Key | Action |
|---|---|
| `x` | export the current view to CSV - whatever filter and sort are applied |
| `o`, or `Enter` on a row | open that row's chart on TradingView, in your default browser |

Exported files land in `%LOCALAPPDATA%\swingdash\Exports`, one per export,
named after the tab/view and timestamped
(`live-rvol_2026-09-15_103205.csv`, `securities-stocks_...csv`).

The chart link opens on whatever machine swingdash is running on - if
you're connected over SSH/remote desktop, that's the remote machine, not
yours. Indices don't have a chart (an index's name isn't a symbol NSE or
TradingView trades under).

**Live RVOL tab**

| Key | Action |
|---|---|
| `s` / `r` | cycle sort column / reverse |
| `/` | filter symbols (Enter applies, Esc clears) |
| `f` | freeze row ordering |
| arrows, PgUp/PgDn, Home/End, `g`/`G` | navigate |

**RVOL** compares today's volume so far with the typical volume by this
time of day. **RVOL-D** compares it with a full average day, like the
original Pine script; the two converge at the close. Outside market hours
the table shows the last session's close.

**Securities tab**

| Key | Action |
|---|---|
| `v` | switch view: Stocks / Indices / ETFs |
| `s` / `r` | cycle sort column / reverse |
| `/` | filter symbol, name or sector (Enter applies, Esc clears) |
| `b` | cycle price band filter (All / 2% / 5% / 10% / 20% / No Band) |
| `m` | show only stocks under exchange surveillance |
| `R` | refresh from NSE (capitalised so it can't fire by accident) |
| arrows, PgUp/PgDn, Home/End, `g`/`G` | navigate |

This data is end-of-day and doesn't refresh on its own - press `R` in the
tab, or run `swingdash refresh` from outside the dashboard. NSE's own files
for a session typically land ~19:50 (price bands) and ~21:00 (surveillance)
IST that evening, so the tab flags when a refresh is probably stale. The
first refresh after install also backfills sector and market cap from
Upstox one call at a time (paced to its rate limit, ~30-45 minutes for the
whole exchange). It saves progress as it goes, so quitting partway through
and refreshing again later picks up where it left off.

**Scanner tab**

| Key | Action |
|---|---|
| `s` / `r` | cycle sort column / reverse (starts on Mswing, strongest first) |
| `/` | filter symbols (Enter applies, Esc clears) |
| `f` | freeze row ordering |
| `i` | pick the benchmark index Mswing compares against (remembered) |
| `p` | show / hide the detail panel |
| arrows, PgUp/PgDn, Home/End, `g`/`G` | navigate |

- **Burst Power** counts close-to-close moves of 5-10%, 10-19% and 19%+
  over the last 3 years (`count5/5 + count10/2 + count19/0.5`, dot green at
  15+, yellow at 10+). It counts **completed days only**: today's move joins
  once the session has closed, never while it's still forming.
- **Mswing** is 20-day plus 50-day momentum, compared with the benchmark
  index (NIFTY MIDSML 400 by default): green is positive and beating the
  index, yellow is positive-but-lagging or negative-but-beating, red is
  negative and lagging. During market hours it uses the live price as
  today's close, as TradingView does.
- The detail panel shows the highlighted symbol's full Burst table (with the
  latest date for each bucket) and the Mswing breakdown.

The tab reads three years of daily candles from the local cache, so it fills
in within seconds. The first open of each trading day fetches one day of
new candles per symbol, paced under Upstox's rate limits - a few hundred
symbols take a minute or two, with the table usable meanwhile. Opening it
again later that day makes no API calls.

**Chartink tab**

| Key | Action |
|---|---|
| `a` | add: paste a chartink.com screener or dashboard link, a request payload, or a scan clause |
| `R` | run the highlighted screener/widget - or every widget of a highlighted dashboard |
| `W` | save the result's NSE symbols as a watchlist (and make it the active one) |
| `m` / `D` | rename / delete (only in swingdash - nothing changes on Chartink) |
| `s` / `r` | cycle sort column (starts in Chartink's order) / reverse |
| `/` | filter rows (Enter applies, Esc clears) |
| `Tab` | move between the list and the results |

What `a` accepts:

- **A screener link** (`https://chartink.com/screener/consolidatedbo`) -
  swingdash reads the scan clause from the page. Works for public screeners.
  If the screener has **custom columns** (say RVOL%, MSwing), a link alone
  can't fetch them - Chartink builds them in your browser - so swingdash
  asks for the screener's request payload too (or skip, to add it without).
- **A screener link, then its payload on the next line** - does the same
  in one paste: custom columns with their Chartink names and colours.
- **A dashboard link** (`https://chartink.com/dashboard/130216`) - pick
  which widgets to import (tables are preselected); they're grouped under
  the dashboard's name. Widgets grouped by stock/sector show their latest
  values; market-breadth widgets (MBI, advances %, stocks above SMAs - no
  grouping) show one row per day, newest first, as far back as the widget
  shows on Chartink.
- **A request payload** - in the browser's DevTools, Network tab, run the
  screener/widget and copy the payload of `screener/process` or
  `widget/process`, in any of its forms ("view source", "view parsed", or a
  Python dict like `{'scan_clause': '...'}`). This is the way to use
  **private** screeners/dashboards. Custom columns come through too, but
  without their names (the payload doesn't carry them) - put the
  screener's link on the line above to get the names and colours.
- **A bare clause** - `( {cash} ( ... ) )` for a screener, `select ...`
  for a widget.

Things to know:

- Nothing is fetched until you press `a` or `R`, and results are saved, so
  reopening the dashboard shows the last results without asking Chartink.
  A dashboard's widgets run one after another, at least a second apart.
- Chartink has no public API; swingdash replays the same requests its
  website makes, without logging in (it never stores Chartink credentials
  or cookies). So data is Chartink's free, delayed data (~5 minutes), and
  if Chartink changes its site the tab reports an error rather than wrong
  numbers. Keep it personal and low-volume.
- **BAND** comes from the Securities tab's data (press `R` there once);
  **BURST** uses the same 3-year daily history as the Scanner, with
  Chartink's `close` as today's price - symbols not cached yet fetch it once.
  Rows grouped by sector/industry, and BSE-only rows, have neither.

**Risk tab**

| Key | Action |
|---|---|
| `S` (or the Settings button) | capital, broker (for charges), risk per trade, max position %, max heat %, ATR / recent-low / liquidity settings |
| `ctrl+t` (or Take trade) | add the sized trade to open positions (adjust the fill and the date taken first) |
| `m` | edit the highlighted position - e.g. trail its stop, or fix the date taken |
| `C` | close it at an exit price and exit date (it moves to closed positions) |
| `D` | delete it (a mistake, not an exit) |
| `h` | switch the table between open and closed positions |
| `B` (or the Dhan button) | sync from your Dhan account: pick the start date, paste a token when needed, bring back hidden rows |
| `Tab` | move between the form, the buttons and the table |

Type a symbol and the form sizes the trade as you go. The entry fills in
with the current price until you type your own. Prices come from the live
feed, else Upstox's quote API (today's last trade, also after the close -
labelled "LTP at 15:32"); a dimmed/yellow "close" is only the last cached
daily close, shown until a quote arrives. Pick the stop as a price, a %
below entry, an ATR multiple or the lowest low of the last N days, and the
risk as a % of capital or a fixed ₹ amount. The quantity is the most shares
that keeps every limit:

- **risk** - the loss if the stop is hit, *including* round-trip delivery
  charges for your broker (Upstox, Dhan or Zerodha - pick it in settings;
  rates verified Sep 2026), stays within the risk per trade;
- **heat** - the total open risk of your positions (Σ (entry − stop) ×
  quantity, zero once a stop is at or above entry) stays within the heat limit;
- **allocation** - one position uses at most the max position % of capital;
- **free capital** - it fits in the capital not already in open positions;
- SME stocks are sized in whole lots.

Warnings flag a stop wider than the stock's price band (a fall to it can
take several locked sessions), exchange surveillance, trade-for-trade
series, and a quantity that's a large share of the average daily volume.
Band and surveillance come from the Securities tab's data - fetch it once.

Positions are the ones you add here, or imported from **Dhan** (below).
Capital is what you set (closing trades doesn't change it). The table shows
the source (M manual, D Dhan), live P&L, R multiple, open risk, **to stop** -
what a fall from the current price to the stop would give back - the plan
check and the date taken (closed positions: dates taken and exited, days
held, and Dhan's actual charges and net P&L). Dates can be typed as
09-09-2026, 9/9/26 or 9 Sep 2026.

**Not followed.** The PLAN column flags an open position with **no SL**, an
**SL hit** (the price is below the stop and it's still held), **oversized**
(more shares than you sized in this tab) or **SL wider** (the stop moved below
the planned one). A position with no usable stop still counts in the heat:
its risk is measured from the current price down to an *assumed* stop
(1.5× ATR by default, or a % - see settings), shown as `~₹` in yellow and as
"(x% assumed)" in the summary. Set a real stop with `m` and the flag clears.

**Dhan.** Dhan's personal Trading API is free and reading your account needs
no static IP (that's only for placing orders - swingdash never does).
1. On web.dhan.co: My Profile > Access DhanHQ APIs > generate an **Access
   Token** (valid 24 hours).
2. In the Risk tab press `B`, enter your client ID and paste the token. It's
   stored in your user folder like the Upstox token, never shown again, and
   swingdash **renews it** while you keep using the app - open it at least
   once a day and you won't need to paste a new one.
3. Pick **Sync trades from** (default: a year back, or the date you used
   last). Choosing an earlier date later - to bring in 2024 trades, say -
   reads only the stretch not read before; a later date leaves older trades
   out (your holdings still account for shares bought before it). The sync
   reads that trade history, today's trades and your holdings, then
   builds positions: open ones from the shares you still hold, and a closed
   one for each day you sold, matching buys and sells first-in-first-out.
   Your stops and notes on imported rows are kept across syncs; quantity,
   prices and dates come from Dhan. Size a trade here first and the Dhan buy
   (within 5 days) takes over that row - keeping the plan, so a bigger
   quantity shows as oversized.

The first look at the tab each day syncs by itself; `B` syncs any time.
`D` on an imported row asks: **Remove** (it comes back on the next sync) or
**Hide for good** (syncs skip it - tick "Bring back hidden positions" in the
`B` dialog to restore). If something can't be matched
(a stock that isn't in the NSE list, or quantities the history can't explain),
the status line says so rather than guessing.

## Other commands

```powershell
swingdash refresh [--no-sectors]        # update the Securities tab's NSE data (and sector backfill)
swingdash doctor --feed                 # also opens the live feed briefly
swingdash replay [SYMBOLS...]           # replays a past session and verifies the RVOL maths
swingdash migrate-legacy --from PATH    # one-time import from an old checkout's data/ folder
```

## Development

```powershell
uv sync                          # creates .venv with dev tools
uv run pre-commit install        # ruff, format, pyright, import-linter on commit
uv run swingdash                 # run from source
uv run pytest                    # offline suite (unit, integration, UI pilot tests)
uv run pytest -m network         # live checks against Upstox (needs a token)
```

For development, a `.env` in the working directory is also read
(see `.env.example`); environment variables take precedence over both.

Architecture and conventions for contributors are in [CLAUDE.md](CLAUDE.md).
