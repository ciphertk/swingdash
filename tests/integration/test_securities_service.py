import datetime as dt
import threading

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.domain.calendar import IST
from swingdash.domain.securities import PriceBand, Surveillance
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.securities import SecuritiesService
from tests.fakes.sources import FakeFundamentals, FakeSecuritiesSource, FixedCalendar

SUNDAY = dt.datetime(2026, 9, 13, 12, 0, tzinfo=IST)
UTC_NOW = dt.datetime(2026, 9, 13, 6, 30, tzinfo=dt.UTC)


def _service(
    db: Database,
    source: FakeSecuritiesSource | None = None,
    upstox: FakeFundamentals | None = None,
    now: dt.datetime = SUNDAY,
    isin_lookup=lambda _symbol: None,
    utc_now: dt.datetime = UTC_NOW,
    backfill_interval: float = 0,
) -> tuple[SecuritiesService, FundamentalsService]:
    fundamentals = FundamentalsService(
        upstox or FakeFundamentals(),
        FundamentalsRepository(db),
        clock=lambda: utc_now,
        backfill_interval=backfill_interval,
    )
    service = SecuritiesService(
        source or FakeSecuritiesSource(),
        SecuritiesRepository(db),
        fundamentals,
        FixedCalendar(now),
        isin_lookup=isin_lookup,
    )
    return service, fundamentals


def test_refresh_joins_stocks_etfs_indices(db: Database):
    service, _ = _service(db)
    result = service.refresh_blocking(include_sectors=False)
    assert result.error is None

    snapshot = service.snapshot()
    stocks = {e.symbol: e for e in snapshot.equities}
    assert "NIFTYBEES" not in stocks  # ETFs trade as EQ but are listed separately
    assert "SREEL" not in stocks and "3IINFOLTD" not in stocks  # BE next session

    raymond = stocks["RAYMOND"]
    assert raymond.name == "Raymond Limited"
    assert raymond.isin == "INE301A01014"
    assert raymond.band is PriceBand.P20
    assert raymond.surveillance == Surveillance(stasm=1)
    assert stocks["RELIANCE"].band is PriceBand.NO_BAND
    assert not stocks["RELIANCE"].surveillance.flagged

    # Moved BE -> EQ: listing details still found although the equity list says BE.
    assert stocks["SADBHIN"].isin == "INE764L01010"

    etfs = {e.symbol: e for e in snapshot.etfs}
    assert etfs["GOLDBEES"].name == "NIPPON INDIA ETF GOLD BEES"  # full fund name from bands
    assert etfs["GOLDBEES"].band is PriceBand.NO_BAND
    assert any(i.name == "NIFTY 50" for i in snapshot.indices)

    bands = snapshot.datasets["bands"]
    assert bands.as_of == dt.date(2026, 9, 11)
    assert bands.fetched_at == SUNDAY
    assert bands.error is None


def test_a_failed_dataset_keeps_its_last_good_copy(db: Database):
    service, _ = _service(db)
    service.refresh_blocking(include_sectors=False)
    first = service.snapshot()

    broken, _ = _service(db, FakeSecuritiesSource(failing={"surveillance"}))
    result = broken.refresh_blocking(include_sectors=False)

    assert result.error is not None and "surveillance" in result.error
    snapshot = broken.snapshot()
    raymond = next(e for e in snapshot.equities if e.symbol == "RAYMOND")
    assert raymond.surveillance == Surveillance(stasm=1)  # previous copy
    status = snapshot.datasets["surveillance"]
    assert status.error is not None and "offline" in status.error
    assert status.fetched_at == first.datasets["surveillance"].fetched_at
    assert snapshot.datasets["bands"].error is None


def test_isin_falls_back_to_the_instrument_list(db: Database):
    source = FakeSecuritiesSource(failing={"listings"})
    service, _ = _service(db, source, isin_lookup=lambda s: f"ISIN-{s}")
    service.refresh_blocking(include_sectors=False)
    raymond = next(e for e in service.snapshot().equities if e.symbol == "RAYMOND")
    assert raymond.isin == "ISIN-RAYMOND"
    assert raymond.name == "RAYMOND LIMITED"  # from the band list


def test_sector_backfill_fills_the_snapshot_and_resumes(db: Database):
    upstox = FakeFundamentals(empty={"INE002A01018"})
    service, _ = _service(db, upstox=upstox)
    service.refresh_blocking(include_sectors=True)

    stocks = {e.symbol: e for e in service.snapshot().equities}
    assert stocks["RAYMOND"].sector == "Sector of INE301A01014"
    assert stocks["RAYMOND"].market_cap_cr == 1000.0
    assert stocks["RELIANCE"].sector is None  # no fundamentals: cached as empty
    first_run = len(upstox.calls)
    assert first_run == len({e.isin for e in stocks.values() if e.isin})

    # Everything is cached now, empty results included: nothing is refetched.
    service.refresh_blocking(include_sectors=True)
    assert len(upstox.calls) == first_run


def test_backfill_pacing_rate_limits_and_failures(db: Database):
    upstox = FakeFundamentals(failing={"B"}, rate_limited_once={"C"})
    _, fundamentals = _service(db, upstox=upstox)
    progress: list[tuple[int, int]] = []

    result = fundamentals.backfill(
        ["A", "B", "C", "A"],
        cancel=threading.Event(),
        min_interval=0,
        rate_limit_pause=0,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert (result.total, result.fetched, result.failed) == (3, 2, 1)
    assert upstox.calls == ["A", "B", "C", "C"]  # C retried after the 429
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    # Failed ISINs aren't cached, so they're retried next time.
    again = fundamentals.backfill(["A", "B", "C"], cancel=threading.Event(), min_interval=0)
    assert again.total == 1


def test_backfill_refetches_only_after_max_age(db: Database):
    upstox = FakeFundamentals()
    _, fundamentals = _service(db, upstox=upstox)
    fundamentals.backfill(["A"], cancel=threading.Event())
    assert fundamentals.backfill(["A"], cancel=threading.Event()).total == 0

    _, month_later = _service(db, upstox=upstox, utc_now=UTC_NOW + dt.timedelta(days=31))
    assert month_later.backfill(["A"], cancel=threading.Event()).total == 1


def test_backfill_gives_up_when_everything_fails(db: Database):
    isins = [f"X{i}" for i in range(50)]
    upstox = FakeFundamentals(failing=set(isins))
    _, fundamentals = _service(db, upstox=upstox)
    result = fundamentals.backfill(isins, cancel=threading.Event(), min_interval=0)
    assert result.gave_up
    assert len(upstox.calls) == 20


def test_cancel_stops_a_background_refresh_promptly(db: Database):
    service, _ = _service(db, backfill_interval=1.2)
    # Real pacing (1.2s per call) would take a minute for the fixture stocks.
    assert service.refresh(include_sectors=True)
    assert not service.refresh()  # single-flight
    deadline = dt.datetime.now() + dt.timedelta(seconds=5)
    while service.progress.phase != "sectors" and dt.datetime.now() < deadline:
        threading.Event().wait(0.02)
    service.cancel(timeout=3)
    assert not service.refreshing


@pytest.mark.parametrize(
    ("fetched_hour", "now", "expected"),
    [
        (22, dt.datetime(2026, 9, 13, 12, 0, tzinfo=IST), False),  # fetched Fri night
        (18, dt.datetime(2026, 9, 13, 12, 0, tzinfo=IST), True),  # fetched before Fri files
    ],
)
def test_newer_data_hint(db: Database, fetched_hour, now, expected):
    fetched_at = dt.datetime(2026, 9, 11, fetched_hour, 0, tzinfo=IST)
    writer, _ = _service(db, now=fetched_at)
    writer.refresh_blocking(include_sectors=False)
    reader, _ = _service(db, now=now)
    assert reader.newer_data_likely() is expected


def test_nothing_fetched_yet(db: Database):
    service, _ = _service(db)
    assert service.snapshot().empty
    assert service.newer_data_likely()
