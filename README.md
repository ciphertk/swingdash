# Swing dashboard

A terminal screener for NSE swing trading. Streams live data from Upstox
and shows **live RVOL** for your watchlists: time-of-day normalised, so
10:30 is compared against a typical 10:30 rather than a whole day.

## Setup

Requires Python 3.12+ and an Upstox **Analytics Token**
(account.upstox.com -> Developer Apps -> your app -> Analytics tab).

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env               # then paste your token into .env
python scripts/test_connection.py    # verifies the token, caches instrument lists
```

## Run

```powershell
python scripts/rvol.py                    # "default" watchlist (or first saved one)
python scripts/rvol.py nxtDay             # a saved watchlist
python scripts/rvol.py -s RELIANCE,TCS    # ad-hoc symbols
```

| Key | Action |
|---|---|
| `w` | pick a watchlist |
| `n` / `e` / `d` | new / edit / delete watchlist (paste symbols; `NSE:` prefixes are fine) |
| `s` / `r` | cycle sort column / reverse |
| `/` | filter symbols (Enter applies, Esc clears) |
| `f` | freeze row ordering |
| arrows, PgUp/PgDn, Home/End, `g`/`G` | navigate |
| `q` | quit |

**RVOL** compares today's volume so far with the typical volume by this
time of day. **RVOL-D** compares it with a full average day, like the
original Pine script; the two converge at the close. Outside market hours
the table shows the last session's close.

## Checks

```powershell
python scripts/test_feed.py      # the token can open the live feed
python scripts/replay_rvol.py    # replays a past session and verifies the maths
```
