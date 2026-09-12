"""
Live RVOL terminal.

    python scripts/rvol.py                  # the "default" saved watchlist
    python scripts/rvol.py banking-swing    # a named saved watchlist
    python scripts/rvol.py -s RELIANCE,TCS  # ad-hoc symbols

Keys: q quit | w watchlist | n new | e edit | s sort | r reverse
      / filter | f freeze order | g/G top/bottom | esc clear
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.live.engine import RvolEngine
from app.services import watchlist_service
from app.tui.app import RvolApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Live RVOL terminal")
    parser.add_argument("watchlist", nargs="?", help="name of a saved watchlist")
    parser.add_argument("-s", "--symbols", help="comma/newline separated symbols, instead of a watchlist")
    args = parser.parse_args()

    if args.symbols:
        symbols = watchlist_service.parse_symbols_text(args.symbols)
        name = None  # ad-hoc list, not one of the saved ones
    elif args.watchlist:
        symbols = watchlist_service.get_watchlist(args.watchlist)
        if symbols is None:
            saved = [w["name"] for w in watchlist_service.list_watchlists()]
            parser.error(f"no watchlist named {args.watchlist!r}. saved: {saved}")
        name = args.watchlist
    else:
        # No argument: prefer "default", else whatever exists, else start
        # empty so the user can create one with 'n' instead of hitting an
        # error just because they deleted the default list.
        name, symbols = _first_available()

    RvolApp(RvolEngine(symbols), watchlist_name=name).run()


def _first_available() -> tuple[str | None, list[str]]:
    saved = watchlist_service.list_watchlists()
    if not saved:
        return None, []
    for entry in saved:
        if entry["name"] == watchlist_service.DEFAULT_WATCHLIST_NAME:
            return entry["name"], entry["symbols"]
    return saved[0]["name"], saved[0]["symbols"]


if __name__ == "__main__":
    main()
