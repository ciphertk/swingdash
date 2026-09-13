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
