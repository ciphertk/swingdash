"""
`swingdash` command-line entry point.

    swingdash                     open the dashboard on the default watchlist
    swingdash run -w nxtDay       open it on a saved watchlist
    swingdash run -s RELIANCE,TCS open it on ad-hoc symbols
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from swingdash import __version__

_SUBCOMMANDS = {"run"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(_with_default_command(argv))
    return int(args.handler(args, parser) or 0)


def _with_default_command(argv: Sequence[str] | None) -> list[str] | None:
    """`swingdash` alone (or with only run-options) means `swingdash run`."""
    if argv is None:
        import sys

        argv = sys.argv[1:]
    args = list(argv)
    if not args or (args[0] not in _SUBCOMMANDS and args[0] not in {"-h", "--help", "--version"}):
        return ["run", *args]
    return args


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swingdash", description="NSE swing-trading dashboard")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="open the dashboard (default)")
    source = run.add_mutually_exclusive_group()
    source.add_argument("-w", "--watchlist", help="name of a saved watchlist")
    source.add_argument("-s", "--symbols", help="comma/newline separated symbols")
    run.set_defaults(handler=_run)

    return parser


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    # Imported here so `--help` and `--version` stay instant and don't need
    # Textual, the Upstox SDK or a database.
    from swingdash.live.engine import RvolEngine
    from swingdash.services import watchlist_service
    from swingdash.tui.app import RvolApp

    if args.symbols:
        symbols = watchlist_service.parse_symbols_text(args.symbols)
        name = None  # ad-hoc list, not one of the saved ones
    elif args.watchlist:
        found = watchlist_service.get_watchlist(args.watchlist)
        if found is None:
            saved = [w["name"] for w in watchlist_service.list_watchlists()]
            parser.error(f"no watchlist named {args.watchlist!r}. saved: {saved}")
        symbols, name = found, args.watchlist
    else:
        name, symbols = _first_available(watchlist_service)

    RvolApp(RvolEngine(symbols), watchlist_name=name).run()
    return 0


def _first_available(watchlist_service) -> tuple[str | None, list[str]]:
    """Prefer "default", else any saved list, else start empty so 'n' can create one."""
    saved = watchlist_service.list_watchlists()
    if not saved:
        return None, []
    for entry in saved:
        if entry["name"] == watchlist_service.DEFAULT_WATCHLIST_NAME:
            return entry["name"], entry["symbols"]
    return saved[0]["name"], saved[0]["symbols"]
