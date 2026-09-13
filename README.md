# swingdash

A terminal dashboard for NSE swing trading, streaming live data from
Upstox. It opens as a set of tabs sharing one global watchlist and one live
feed. The first tab is **Live RVOL**: time-of-day normalised, so 10:30 is
compared against a typical 10:30 rather than a whole day.

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

## Other commands

```powershell
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
