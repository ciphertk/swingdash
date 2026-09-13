"""ChartinkService against a real SQLite DB, fake Chartink/NSE/Upstox sources and a fixed clock."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.adapters.storage.repos.chartink import ChartinkRepository
from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import IST
from swingdash.domain.chartink import (
    ChartinkInputError,
    ChartinkKind,
    ChartinkRequest,
    ChartinkResult,
    ChartinkRow,
    ImportTarget,
    ScreenerDef,
)
from swingdash.domain.securities import PriceBand
from swingdash.services.candles import CandleService
from swingdash.services.chartink import ChartinkService
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.instruments import InstrumentService
from swingdash.services.securities import SecuritiesService
from tests.fakes.chartink import FakeChartinkSource
from tests.fakes.sources import FakeFundamentals, FakeHistory, FakeSecuritiesSource, FixedCalendar

SUNDAY = dt.datetime(2026, 9, 13, 12, 0, tzinfo=IST)
FRIDAY = dt.date(2026, 9, 11)
SYMBOLS = ["RAYMOND", "TBZ", "RELIANCE", "GOLDBEES"]
SCREENER = ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": "( {cash} ( stocks ) )"})


def _key(symbol: str) -> str:
    return f"NSE_EQ|{symbol}"


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _history(drift: float) -> list[DailyBar]:
    bars, price, day = [], 100.0, FRIDAY - dt.timedelta(days=400)
    while day <= FRIDAY:
        if day.weekday() < 5:
            price *= 1 + drift
            # A 12% day every so often, so Burst Power has something to count.
            if day.day == 15:
                price *= 1.12
            bars.append(DailyBar(day.isoformat(), price, price * 1.01, price * 0.99, price, 1e5))
        day += dt.timedelta(days=1)
    return bars


def _stocks(*symbols: str, close: float = 100.0) -> ChartinkResult:
    return ChartinkResult(
        ("nsecode", "name", "close"),
        tuple(ChartinkRow(s, {"nsecode": s, "name": s.title(), "close": close}) for s in symbols),
        group_by="symbol",
    )


@pytest.fixture
def chartink(db: Database, tmp_path: Path):
    equities = tmp_path / "equities.json"
    equities.write_text(
        json.dumps(
            [
                {"instrument_key": _key(s), "trading_symbol": s, "name": s, "isin": ""}
                for s in SYMBOLS
            ]
        )
    )
    instruments = InstrumentService(equities, tmp_path / "indices.json")
    calendar = FixedCalendar(SUNDAY)
    candles = CandleService(FakeHistory(), CandleRepository(db), calendar)
    for symbol in SYMBOLS:
        CandleRepository(db).upsert(_key(symbol), _history(0.002))

    securities = SecuritiesService(
        FakeSecuritiesSource(),
        SecuritiesRepository(db),
        FundamentalsService(FakeFundamentals(), FundamentalsRepository(db), backfill_interval=0),
        calendar,
    )
    source = FakeChartinkSource()
    service = ChartinkService(
        source,
        ChartinkRepository(db),
        instruments=instruments,
        securities=securities,
        candles=candles,
        calendar=calendar,
    )
    yield service, source, securities
    service.close()


def _run(service: ChartinkService, item_id: int) -> None:
    service.run([item_id])
    _wait(lambda: not service.status.busy and service.get(item_id) is not None)
    _wait(
        lambda: (
            (item := service.get(item_id)) is not None and (item.result or item.error) is not None
        )
    )


def test_saved_items_and_results_survive_a_restart(chartink, db: Database):
    service, source, _ = chartink
    item = service.add("Smart money", SCREENER)
    _run(service, item.id)

    reopened = ChartinkService(
        source,
        ChartinkRepository(db),
        instruments=service._instruments,
        securities=service._securities,
        candles=CandleService(FakeHistory(), CandleRepository(db), FixedCalendar(SUNDAY)),
        calendar=FixedCalendar(SUNDAY),
    )
    try:
        saved = reopened.get(item.id)
        assert saved is not None and saved.name == "Smart money"
        assert saved.result is not None and saved.result.rows[0].key == "LT"
        assert saved.fetched_at == SUNDAY
        assert len(source.calls) == 1  # nothing re-run on reopen
    finally:
        reopened.close()


def test_stock_rows_get_price_band_and_burst_power(chartink):
    service, source, securities = chartink
    securities.refresh_blocking(include_sectors=False)
    source.scripted["stocks"] = _stocks("RAYMOND", "TBZ", "RELIANCE", "GOLDBEES", "NOTLISTED")
    item = service.add("Mine", SCREENER)
    _run(service, item.id)

    _wait(lambda: (view := service.view(item.id)) is not None and not view.history_pending)
    view = service.view(item.id)
    assert view is not None and view.enriched and view.bands_available
    by_key = {row.row.key: row for row in view.rows}
    assert by_key["RAYMOND"].band is PriceBand.P20
    assert by_key["TBZ"].band is PriceBand.P5
    assert by_key["GOLDBEES"].band is PriceBand.NO_BAND  # ETFs have bands too
    assert by_key["RAYMOND"].burst is not None and by_key["RAYMOND"].burst.count_10pct > 0
    assert by_key["NOTLISTED"].symbol is None and by_key["NOTLISTED"].burst is None
    assert service.symbols(item.id) == ["RAYMOND", "TBZ", "RELIANCE", "GOLDBEES"]


def test_bands_are_missing_until_securities_data_exists(chartink):
    service, source, _ = chartink
    source.scripted["stocks"] = _stocks("RAYMOND")
    item = service.add("Mine", SCREENER)
    _run(service, item.id)
    view = service.view(item.id)
    assert view is not None and not view.bands_available
    assert view.rows[0].band is None


def test_group_widgets_are_shown_as_is(chartink):
    service, _, _ = chartink
    widget = ChartinkRequest(
        ChartinkKind.WIDGET, {"query": "select 1 as 'x' WHERE {cash} 1 = 1 GROUP BY sector"}
    )
    item = service.add("Sectors", widget)
    _run(service, item.id)
    view = service.view(item.id)
    assert view is not None and not view.enriched
    assert view.rows[0].row.key == "co-working"
    assert service.symbols(item.id) == []


def test_a_failed_run_keeps_the_last_good_result(chartink):
    service, source, _ = chartink
    item = service.add("Smart money", SCREENER)
    _run(service, item.id)

    source.scripted["stocks"] = RuntimeError("Chartink refused the request (429)")
    service.run([item.id])
    _wait(lambda: (i := service.get(item.id)) is not None and i.error is not None)

    saved = service.get(item.id)
    assert saved is not None
    assert saved.error == "Chartink refused the request (429)"
    assert saved.result is not None and saved.result.rows  # still there


def test_runs_queue_one_after_another_without_duplicates(chartink):
    service, source, _ = chartink
    first = service.add("A", ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": "( a )"}))
    second = service.add("B", ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": "( b )"}))
    source.release.clear()  # hold the first run

    assert service.run([first.id, second.id]) == 2
    _wait(lambda: service.status.running_id == first.id)
    assert service.run([first.id, second.id]) == 0  # already running / queued
    source.release.set()

    _wait(lambda: not service.status.busy)
    assert [call[1] for call in source.calls] == ["( a )", "( b )"]


def test_dashboard_import_groups_widgets_under_a_unique_name(chartink):
    service, _, _ = chartink
    url = "https://chartink.com/dashboard/130216"
    dashboard = service.fetch_dashboard(url)
    tables = [w for w in dashboard.widgets if w.is_table]

    imported = service.add_widgets(dashboard, tables, url)
    again = service.add_widgets(dashboard, tables[:1], url)

    assert {i.collection for i in imported} == {"Swing trade Dashboard"}
    assert [i.name for i in imported] == ["Stocks near 52 week high", "Price EMA SMA 50 Days"]
    assert again[0].collection == "Swing trade Dashboard (2)"
    assert all(i.request.fields["size"] == "1" for i in imported)
    assert len(service.collection_items("Swing trade Dashboard")) == 2

    service.delete_collection("Swing trade Dashboard")
    assert [i.collection for i in service.items()] == ["Swing trade Dashboard (2)"]


def test_screener_link_import_and_private_screeners(chartink):
    service, _, _ = chartink
    parsed = service.parse("https://chartink.com/screener/consolidatedbo")
    assert isinstance(parsed, ImportTarget)
    screener = service.fetch_screener(parsed.url)
    item = service.add_screener(screener, parsed.url)
    assert item.name == "consolidatedBO"
    assert item.request.fields["scan_clause"].startswith("( {57960}")

    private = ScreenerDef(1, "Secret", "secret", is_private=True, clause=None)
    with pytest.raises(ChartinkInputError, match="network tab"):
        service.add_screener(private, "https://chartink.com/screener/secret")


PAYLOAD_WITH_COLUMNS = (
    Path(__file__).parents[1] / "fixtures" / "chartink" / "screener_payload_columns.json"
)


def test_link_with_payload_keeps_custom_columns_named_and_coloured(chartink, db: Database):
    service, source, _ = chartink
    url = "https://chartink.com/screener/total-universe-v2"
    payload = service.parse(PAYLOAD_WITH_COLUMNS.read_text(encoding="utf-8"))
    assert isinstance(payload, ChartinkRequest)
    item = service.add_screener(service.fetch_screener(url), url, payload=payload)
    _run(service, item.id)

    assert source.calls[-1][0] == "screener"
    saved = ChartinkRepository(db).get(item.id)  # names survive a restart
    assert saved is not None and saved.missing_columns == []
    assert saved.columns["_7b5fd"].name == "RVOL%"
    assert saved.columns["_d20ef"].colors == ("#4CAF50FF", None)
    assert saved.request.fields["column_clause"] == payload.fields["column_clause"]
    assert saved.result is not None and "_d20ef" in saved.result.columns


def test_link_alone_says_which_custom_columns_it_is_missing(chartink):
    service, _, _ = chartink
    url = "https://chartink.com/screener/total-universe-v2"
    item = service.add_screener(service.fetch_screener(url), url)
    assert "column_clause" not in item.request.fields
    assert item.missing_columns == ["RVOL%", "MSwing"]


def test_a_widget_payload_cannot_stand_in_for_a_screener(chartink):
    service, _, _ = chartink
    url = "https://chartink.com/screener/total-universe-v2"
    widget = ChartinkRequest(ChartinkKind.WIDGET, {"query": "select 1"})
    with pytest.raises(ChartinkInputError, match="widget"):
        service.add_screener(service.fetch_screener(url), url, payload=widget)


def test_rename_and_delete(chartink):
    service, _, _ = chartink
    item = service.add("Old", SCREENER)
    version = service.version
    service.rename(item.id, "  New  ")
    assert service.get(item.id).name == "New"  # type: ignore[union-attr]
    assert service.version > version
    service.delete(item.id)
    assert service.items() == []
