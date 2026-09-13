"""
`swingdash` command-line interface.

    swingdash                        open the dashboard
    swingdash run -w nxtDay          ...on a saved watchlist
    swingdash run -s RELIANCE,TCS    ...on ad-hoc symbols
    swingdash setup                  token, database, instrument list
    swingdash doctor [--feed]        check the installation
    swingdash replay [SYMBOL ...]    verify the RVOL maths on a past session
    swingdash migrate-legacy --from PATH
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import platform
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from swingdash import __version__

_COMMANDS = {"run", "setup", "doctor", "replay", "migrate-legacy"}
_SMOKE_TEST_KEYS = ["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"]  # RELIANCE, TCS

Handler = Callable[[argparse.Namespace, argparse.ArgumentParser], int]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(_with_default_command(argv))
    handler: Handler = args.handler
    return handler(args, parser)


def _with_default_command(argv: Sequence[str] | None) -> list[str]:
    """`swingdash` alone (or with only run-options) means `swingdash run`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or (args[0] not in _COMMANDS and args[0] not in {"-h", "--help", "--version"}):
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

    setup = commands.add_parser("setup", help="configure token, database and instrument list")
    setup.set_defaults(handler=_setup)

    doctor = commands.add_parser("doctor", help="check the installation")
    doctor.add_argument("--feed", action="store_true", help="also open the live feed briefly")
    doctor.set_defaults(handler=_doctor)

    replay = commands.add_parser("replay", help="verify RVOL maths against a past session")
    replay.add_argument("symbols", nargs="*", default=["RELIANCE", "TCS", "HDFCBANK"])
    replay.set_defaults(handler=_replay)

    legacy = commands.add_parser("migrate-legacy", help="import data from a pre-0.1 repo checkout")
    legacy.add_argument("--from", dest="source", type=Path, default=Path.cwd(), metavar="PATH")
    legacy.add_argument("--force", action="store_true", help="replace an existing database")
    legacy.set_defaults(handler=_migrate_legacy)

    return parser


# --- commands --------------------------------------------------------------
# Heavy imports stay inside handlers so --help and --version are instant.


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from swingdash.bootstrap import build_services
    from swingdash.domain.watchlist import Watchlist, parse_symbols_text
    from swingdash.logging_setup import configure_logging
    from swingdash.settings import load_settings
    from swingdash.ui.app import SwingDashApp

    settings = load_settings()
    configure_logging(settings.paths, settings.upstox_token)
    if not settings.upstox_token:
        print(
            f"error: no Upstox token configured - run `swingdash setup` (looked in {settings.paths.env_file})"
        )
        return 1

    services = build_services(settings)
    try:
        if not services.instruments.available():
            print("error: NSE instrument list not downloaded - run `swingdash setup`")
            return 1

        if args.symbols:
            # Ad-hoc symbols: shown in the tabs, never saved.
            watchlist = Watchlist("(ad-hoc)", tuple(parse_symbols_text(args.symbols)))
        else:
            watchlist = services.watchlists.initial(args.watchlist)
            if args.watchlist and watchlist is None:
                saved = [w.name for w in services.watchlists.all()]
                parser.error(f"no watchlist named {args.watchlist!r}. saved: {saved}")

        SwingDashApp(services, initial_watchlist=watchlist).run()
        return 0
    finally:
        services.close()


def _setup(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from dotenv import set_key

    from swingdash.adapters.upstox.client import UpstoxClient
    from swingdash.adapters.upstox.instruments import download_instrument_masters
    from swingdash.adapters.upstox.quotes import UpstoxQuotes
    from swingdash.bootstrap import build_services
    from swingdash.logging_setup import configure_logging
    from swingdash.settings import NSE_INSTRUMENTS_URL, TOKEN_ENV, load_settings

    settings = load_settings()
    settings.paths.ensure()
    configure_logging(settings.paths, settings.upstox_token)
    print(f"config:   {settings.paths.config_dir}")
    print(f"data:     {settings.paths.data_dir}")

    if not settings.upstox_token:
        if sys.stdin.isatty():
            token = getpass.getpass("Upstox Analytics Token (input hidden): ").strip()
            if token:
                settings.paths.env_file.touch(exist_ok=True)
                set_key(str(settings.paths.env_file), TOKEN_ENV, token)
                with contextlib.suppress(OSError):
                    os.chmod(settings.paths.env_file, 0o600)
                settings = replace(settings, upstox_token=token)
                print(f"token:    saved to {settings.paths.env_file}")
        else:
            print(f"token:    not set - add {TOKEN_ENV}=... to {settings.paths.env_file}")

    services = build_services(settings)
    try:
        print(f"database: {settings.paths.database} (ready)")
        print("instruments: downloading NSE instrument master ...")
        masters = download_instrument_masters(NSE_INSTRUMENTS_URL)
        services.instruments.store(masters.equities, masters.indices)
        print(
            f"instruments: {len(masters.equities)} equities, {len(masters.indices)} indices cached"
        )
    finally:
        services.close()

    if not settings.upstox_token:
        return 1
    try:
        UpstoxQuotes(UpstoxClient(settings.require_token)).ltp(_SMOKE_TEST_KEYS[:1])
    except Exception as exc:
        print(f"token:    REJECTED by Upstox ({type(exc).__name__}) - check the value")
        return 1
    print("token:    OK")
    print("\nReady. Run `swingdash` to open the dashboard.")
    return 0


def _doctor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from swingdash.adapters.storage.db import Database
    from swingdash.adapters.storage.migrations import LATEST_VERSION, schema_version
    from swingdash.adapters.upstox.client import UpstoxClient
    from swingdash.adapters.upstox.quotes import UpstoxQuotes
    from swingdash.settings import load_settings

    settings = load_settings()
    paths = settings.paths
    ok = True

    def line(label: str, value: str, good: bool = True) -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {label:<12} {value}")

    print(f"swingdash {__version__} on Python {platform.python_version()} ({platform.system()})")
    token = settings.upstox_token
    line("token", f"****{token[-4:]}" if token else "missing - run `swingdash setup`", bool(token))
    line("config", str(paths.env_file), True)

    if paths.database.is_file():
        db = Database(paths.database)
        try:
            version = schema_version(db)
            conn = db.connection()
            watchlists = conn.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0]
            baselines = conn.execute("SELECT COUNT(*) FROM rvol_baseline").fetchone()[0]
        finally:
            db.close()
        line(
            "database",
            f"{paths.database} (schema v{version}/{LATEST_VERSION}, "
            f"{watchlists} watchlists, {baselines} baselines)",
            version == LATEST_VERSION,
        )
    else:
        line("database", f"{paths.database} missing - run `swingdash setup`", False)

    line(
        "instruments",
        str(paths.equity_instruments)
        if paths.equity_instruments.is_file()
        else "missing - run `swingdash setup`",
        paths.equity_instruments.is_file(),
    )
    line("logs", str(paths.log_file), True)

    if token:
        try:
            prices = UpstoxQuotes(UpstoxClient(settings.require_token)).ltp(_SMOKE_TEST_KEYS[:1])
            line("upstox api", f"reachable, RELIANCE ltp {next(iter(prices.values()), '?')}", True)
        except Exception as exc:
            line("upstox api", f"FAILED ({type(exc).__name__})", False)
        if args.feed:
            ok = _check_feed(UpstoxClient(settings.require_token), line) and ok

    return 0 if ok else 1


def _check_feed(client, line: Callable[[str, str, bool], None], timeout: float = 15) -> bool:
    from swingdash.adapters.upstox.feed import UpstoxFeedTransport

    opened, ticked = threading.Event(), threading.Event()
    feed = UpstoxFeedTransport(
        client,
        on_tick=lambda *_: ticked.set(),
        on_connection=lambda state: opened.set() if state == "connected" else None,
    )
    feed.start(_SMOKE_TEST_KEYS)
    try:
        good = opened.wait(timeout) and ticked.wait(timeout)
    finally:
        feed.stop()
    line("live feed", "connected, data received" if good else "no data within timeout", good)
    return good


def _replay(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from swingdash.bootstrap import build_services
    from swingdash.logging_setup import configure_logging
    from swingdash.services.rvol.replay import replay_symbol
    from swingdash.settings import load_settings

    settings = load_settings()
    configure_logging(settings.paths, settings.upstox_token)
    services = build_services(settings)
    try:
        results = [
            replay_symbol(
                symbol.upper(),
                instruments=services.instruments,
                history=services.history,
                calendar=services.calendar,
            )
            for symbol in args.symbols
        ]
    finally:
        services.close()

    for result in results:
        if result.error:
            print(f"\n=== {result.symbol}: {result.error}")
            continue
        verdict = "PASS" if result.passed else "FAIL"
        print(
            f"\n=== {result.symbol}  {result.session_date} ({result.days_used}d baseline)  {verdict}"
        )
        print(
            f"  identity   curve[-1] {result.curve_last:,.0f} vs avg daily "
            f"{result.avg_daily_volume:,.0f}  ({result.identity_diff_pct:.2f}%)"
        )
        print(
            f"  converge   intraday {result.final_intraday:.4f}x = day {result.final_day:.4f}x  "
            f"({'yes' if result.converged else 'NO'})"
        )
        if result.legacy_ratio is not None:
            print(f"  legacy     {result.legacy_ratio:.4f}x  ({result.legacy_diff_pct:.2f}% apart)")
        print(
            "  "
            + "  ".join(
                f"{c.label} {c.ratio:.2f}x" if c.ratio is not None else f"{c.label} -"
                for c in result.checkpoints
            )
        )

    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} symbols passed all assertions")
    return 0 if passed == len(results) else 1


def _migrate_legacy(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from swingdash.adapters.storage.db import Database
    from swingdash.adapters.storage.legacy_import import import_legacy
    from swingdash.adapters.storage.migrations import migrate
    from swingdash.settings import load_settings

    settings = load_settings()
    settings.paths.ensure()
    try:
        report = import_legacy(
            args.source.resolve(),
            database=settings.paths.database,
            equity_instruments=settings.paths.equity_instruments,
            index_instruments=settings.paths.index_instruments,
            env_file=settings.paths.env_file,
            force=args.force,
        )
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"error: {exc}")
        return 1

    db = Database(report.database)
    try:
        version = migrate(db)
        conn = db.connection()
        watchlists = [row[0] for row in conn.execute("SELECT name FROM watchlists ORDER BY name")]
        baselines = conn.execute("SELECT COUNT(*) FROM rvol_baseline").fetchone()[0]
    finally:
        db.close()

    if report.backed_up_existing:
        print(f"backed up existing database to {report.backed_up_existing}")
    for item in report.copied:
        print(f"copied   {item}")
    for item in report.skipped:
        print(f"skipped  {item}")
    print(
        f"database schema v{version}: {len(watchlists)} watchlists {watchlists}, {baselines} baselines"
    )
    print("The source files were not modified.")
    return 0
