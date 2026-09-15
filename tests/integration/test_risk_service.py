"""RiskService against a real SQLite DB, fake feed, fake NSE/Upstox sources and a fixed clock."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.adapters.storage.repos.positions import PositionRepository
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.calendar import IST
from swingdash.domain.risk.charges import Broker
from swingdash.domain.risk.sizing import Limit, RiskMode, RiskSpec
from swingdash.domain.risk.stops import RiskInputError, StopMethod
from swingdash.domain.securities import PriceBand
from swingdash.services.candles import CandleService
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.instruments import InstrumentService
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.risk import RiskService
from swingdash.services.risk_settings import RiskSettings, RiskSettingsService
from swingdash.services.securities import SecuritiesService
from tests.fakes.feed import FakeFeedFactory
from tests.fakes.sources import (
    FakeFundamentals,
    FakeHistory,
    FakeQuotes,
    FakeSecuritiesSource,
    FixedCalendar,
)

MONDAY = dt.datetime(2026, 9, 14, 12, 0, tzinfo=IST)
FRIDAY = dt.date(2026, 9, 11)
# (symbol, series, lot size)
STOCKS = [("RAYMOND", "EQ", 1), ("TBZ", "EQ", 1), ("SMEONE", "SM", 600), ("FRESH", "BE", 1)]
SETTINGS = RiskSettings(capital=1_000_000)


def _key(symbol: str) -> str:
    return f"NSE_EQ|{symbol}"


def _history(close: float = 500.0, days: int = 60, through: dt.date = FRIDAY) -> list[DailyBar]:
    """A range of 10 around a flat close, 1 lakh shares a day."""
    bars, day = [], through - dt.timedelta(days=days)
    while day <= through:
        if day.weekday() < 5:
            bars.append(DailyBar(day.isoformat(), close, close + 5, close - 5, close, 100_000))
        day += dt.timedelta(days=1)
    return bars


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


@dataclass
class Harness:
    service: RiskService
    feed: FakeFeedFactory
    history: FakeHistory
    securities: SecuritiesService
    db: Database
    quotes: FakeQuotes
    calendar: FixedCalendar


@pytest.fixture
def harness(db: Database, tmp_path: Path):
    equities = tmp_path / "equities.json"
    equities.write_text(
        json.dumps(
            [
                {
                    "instrument_key": _key(s),
                    "trading_symbol": s,
                    "name": s,
                    "isin": "",
                    "series": series,
                    "lot_size": str(lot),
                }
                for s, series, lot in STOCKS
            ]
        )
    )
    calendar = FixedCalendar(MONDAY)
    history = FakeHistory(daily={_key("FRESH"): _history(close=100)})
    candles = CandleService(history, CandleRepository(db), calendar)
    for symbol in ("RAYMOND", "TBZ", "SMEONE"):
        CandleRepository(db).upsert(_key(symbol), _history())
    securities = SecuritiesService(
        FakeSecuritiesSource(),
        SecuritiesRepository(db),
        FundamentalsService(FakeFundamentals(), FundamentalsRepository(db), backfill_interval=0),
        calendar,
    )
    feed = FakeFeedFactory()
    quotes = FakeQuotes()
    settings = RiskSettingsService(AppStateRepository(db))
    settings.save(SETTINGS)
    service = RiskService(
        PositionRepository(db),
        settings,
        instruments=InstrumentService(equities, tmp_path / "indices.json"),
        candles=candles,
        securities=securities,
        calendar=calendar,
        hub=MarketDataHub(feed),
        quotes=quotes,
    )
    yield Harness(service, feed, history, securities, db, quotes, calendar)
    service.close()


def test_sizing_uses_settings_cached_close_and_nse_flags(harness: Harness):
    harness.securities.refresh_blocking(include_sectors=False)
    info = harness.service.info("raymond")
    assert info is not None and info.quote is not None
    # No feed tick and no quote: the last cached close, marked as such.
    assert (info.quote.price, info.quote.source, info.quote.as_of) == (500, "close", "2026-09-11")
    assert info.band is PriceBand.P20 and info.surveillance.flagged
    assert info.avg_volume == 100_000

    trade = harness.service.size("RAYMOND", 500, StopMethod.PERCENT, 10)
    assert trade.stop == 450
    assert trade.result.limited_by is Limit.RISK
    assert 190 < trade.result.quantity < 200  # ₹10,000 risk less charges
    assert any("surveillance" in w.message for w in trade.warnings)

    # ATR(14) of a flat 10-point range is 10: 1.5 ATR below 500.
    assert harness.service.size("RAYMOND", 500, StopMethod.ATR, 1.5).stop == 485
    assert harness.service.size("RAYMOND", 500, StopMethod.RECENT_LOW, 10).stop == 495

    fixed = harness.service.size(
        "RAYMOND", 500, StopMethod.PRICE, 450, RiskSpec(RiskMode.AMOUNT, 2_500)
    )
    assert fixed.result.quantity < 50


def test_unknown_symbols_and_unset_capital_explain_themselves(harness: Harness):
    with pytest.raises(RiskInputError, match="isn't an NSE stock"):
        harness.service.size("NOPE", 500, StopMethod.PERCENT, 5)
    harness.service.save_settings(replace(SETTINGS, capital=0))
    with pytest.raises(RiskInputError, match="capital"):
        harness.service.size("RAYMOND", 500, StopMethod.PERCENT, 5)


def test_sme_lots_and_trade_for_trade_series(harness: Harness):
    sme = harness.service.size("SMEONE", 500, StopMethod.PERCENT, 10)
    assert sme.result.quantity % 600 == 0

    harness.service.watch("FRESH")  # not cached yet: fetched in the background
    _wait(lambda: (info := harness.service.info("FRESH")) is not None and bool(info.bars))
    fresh = harness.service.size("FRESH", 100, StopMethod.PERCENT, 5)
    assert any("Trade-for-trade series BE" in w.message for w in fresh.warnings)
    assert len(harness.history.daily_calls) == 1


def test_open_positions_count_against_heat_and_free_capital(harness: Harness):
    service = harness.service
    first = service.open_position("RAYMOND", 1_000, 500, 480)  # ₹20,000 risk, ₹5L invested
    service.open_position("TBZ", 500, 500, 470)  # ₹15,000 risk, ₹2.5L invested

    summary = service.portfolio().summary
    assert summary.heat == 35_000 and summary.heat_pct == pytest.approx(3.5)
    assert summary.heat_left == 25_000  # 6% of ₹10L = ₹60,000
    assert summary.free_capital == 250_000

    # A wide stop now hits free capital before anything else.
    trade = service.size("SMEONE", 50, StopMethod.PERCENT, 2)
    assert trade.result.limited_by in (Limit.ALLOCATION, Limit.FREE_CAPITAL)

    # Trailing the stop to break-even takes RAYMOND out of the heat.
    service.update_position(first.id, quantity=1_000, entry=500, stop=505)
    assert service.portfolio().summary.heat == 15_000
    assert service.positions()[0].initial_stop == 480


def test_heat_limit_shrinks_new_trades(harness: Harness):
    service = harness.service
    service.save_settings(replace(SETTINGS, max_heat_pct=1.5, max_allocation_pct=100))
    service.open_position("TBZ", 200, 500, 450)  # ₹10,000 of a ₹15,000 heat limit
    trade = service.size("RAYMOND", 500, StopMethod.PERCENT, 10)
    assert trade.result.limited_by is Limit.HEAT
    assert trade.result.total_risk <= 5_000
    assert any("heat limit" in w.message for w in trade.warnings)


def test_live_prices_come_from_the_feed(harness: Harness):
    service = harness.service
    position = service.open_position("RAYMOND", 100, 500, 450)
    transport = harness.feed.transport
    assert _key("RAYMOND") in (transport.started_with or [])

    transport.tick(_key("RAYMOND"), ltp=530.0)
    row = service.portfolio().open[0]
    assert row.quote is not None and (row.quote.price, row.quote.live) == (530.0, True)
    assert row.position.pnl(row.quote.price) == 3_000
    assert row.days_held == 0  # opened today, on the fixed clock
    assert position.id == row.position.id


def test_close_and_delete_positions(harness: Harness):
    service = harness.service
    kept = service.open_position("RAYMOND", 100, 500, 450)
    gone = service.open_position("TBZ", 10, 500, 450)
    service.close_position(kept.id, 560)
    service.delete_position(gone.id)

    view = service.portfolio()
    assert view.open == ()
    assert [p.symbol for p in view.closed] == ["RAYMOND"]
    assert view.summary.realised_pnl == 6_000
    assert harness.feed.transport.stopped or not view.open  # nothing left to stream

    with pytest.raises(RiskInputError):
        service.open_position("RAYMOND", 10, 500, 520)  # stop above entry on open
    with pytest.raises(RiskInputError):
        service.close_position(kept.id, 0)


def test_settings_survive_a_restart(harness: Harness):
    harness.service.save_settings(
        replace(SETTINGS, risk_mode=RiskMode.AMOUNT, risk_amount=7_500, max_heat_pct=8)
    )
    reloaded = RiskSettingsService(AppStateRepository(harness.db)).get()
    assert reloaded.risk == RiskSpec(RiskMode.AMOUNT, 7_500)
    assert reloaded.max_heat_pct == 8 and reloaded.capital == 1_000_000


def test_unreadable_settings_fall_back_to_defaults(db: Database):
    AppStateRepository(db).set("risk_settings", "{not json")
    assert RiskSettingsService(AppStateRepository(db)).get() == RiskSettings()


def test_quote_api_price_replaces_the_stale_close(harness: Harness):
    # History stops at Friday; the quote API has today's last trade.
    harness.quotes.prices = {_key("RAYMOND"): 481.5}
    harness.service.info("RAYMOND")  # asks for a quote in the background
    _wait(
        lambda: (
            (i := harness.service.info("RAYMOND")) is not None
            and i.quote is not None
            and i.quote.source == "quote"
        )
    )
    info = harness.service.info("RAYMOND")
    assert info is not None and info.quote is not None
    assert (info.quote.price, info.quote.as_of) == (481.5, "12:00")

    # A later feed tick wins over the quote.
    harness.service.open_position("RAYMOND", 10, 500, 450)
    harness.feed.transport.tick(_key("RAYMOND"), ltp=483.0)
    row = harness.service.portfolio().open[0]
    assert row.quote is not None and (row.quote.price, row.quote.source) == (483.0, "live")


def test_quotes_are_asked_for_at_most_once_a_minute(harness: Harness):
    harness.quotes.prices = {_key("RAYMOND"): 481.5}
    for _ in range(5):
        harness.service.info("RAYMOND")
    _wait(lambda: harness.service.info("RAYMOND").quote.source == "quote")  # type: ignore[union-attr]
    assert len(harness.quotes.calls) == 1


def test_a_failing_quote_api_falls_back_and_says_so_once(harness: Harness):
    harness.quotes.failing = True
    harness.service.info("RAYMOND")
    _wait(lambda: bool(harness.quotes.calls) and not harness.service._quotes_in_flight)
    info = harness.service.info("RAYMOND")
    assert info is not None and info.quote is not None and info.quote.source == "close"
    assert any("quotes unavailable" in e for e in harness.service.events())


def test_trade_dates(harness: Harness):
    service = harness.service
    taken = dt.date(2026, 9, 9)
    position = service.open_position("RAYMOND", 10, 500, 450, opened_on=taken)
    assert position.opened_on == taken
    assert service.portfolio().open[0].days_held == 5  # Mon 14 Sep on the fixed clock

    with pytest.raises(RiskInputError, match="future"):
        service.open_position("TBZ", 10, 500, 450, opened_on=dt.date(2026, 9, 15))
    with pytest.raises(RiskInputError, match="before the date taken"):
        service.close_position(position.id, 520, dt.date(2026, 9, 8))
    with pytest.raises(RiskInputError, match="future"):
        service.close_position(position.id, 520, dt.date(2026, 9, 20))

    service.update_position(
        position.id, quantity=10, entry=500, stop=460, opened_on=dt.date(2026, 9, 10)
    )
    service.close_position(position.id, 520, dt.date(2026, 9, 11))
    closed = service.portfolio().closed[0]
    assert (closed.opened_on, closed.closed_on) == (dt.date(2026, 9, 10), dt.date(2026, 9, 11))


def test_the_chosen_brokers_charges_go_into_the_risk(harness: Harness):
    service = harness.service
    upstox = service.size("RAYMOND", 500, StopMethod.PERCENT, 10)
    service.save_settings(replace(SETTINGS, broker=Broker.DHAN))
    dhan = service.size("RAYMOND", 500, StopMethod.PERCENT, 10)
    assert dhan.result.charges.total < upstox.result.charges.total  # no ₹20 per order
    assert RiskSettingsService(AppStateRepository(harness.db)).get().broker is Broker.DHAN
