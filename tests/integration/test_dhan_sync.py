"""DhanSyncService against a real SQLite DB, a fake broker and a fixed clock."""

from __future__ import annotations

import datetime as dt
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.broker_trades import BrokerTradeRepository
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.adapters.storage.repos.positions import PositionRepository
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.domain.broker import BrokerCredentials, BrokerHolding, BrokerToken, BrokerTrade, Side
from swingdash.domain.calendar import IST
from swingdash.domain.risk.discipline import Breach
from swingdash.services.broker_credentials import BrokerCredentialsStore
from swingdash.services.candles import CandleService
from swingdash.services.dhan_sync import DhanSyncService
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.instruments import InstrumentService
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.risk import RiskService
from swingdash.services.risk_settings import RiskSettings, RiskSettingsService
from swingdash.services.securities import SecuritiesService
from tests.fakes.broker import FakeBroker
from tests.fakes.feed import FakeFeedFactory
from tests.fakes.sources import (
    FakeFundamentals,
    FakeHistory,
    FakeQuotes,
    FakeSecuritiesSource,
    FixedCalendar,
)

NOW = dt.datetime(2026, 9, 15, 16, 0, tzinfo=IST)
TODAY = NOW.date()
STOCKS = {"GRAPHITE": "INE371A01025", "HFCL": "INE548A01028", "TCS": "INE467B01029"}
_ids = itertools.count(1)


def _key(symbol: str) -> str:
    return f"NSE_EQ|{STOCKS[symbol]}"


def _fill(side: Side, symbol: str, qty: int, price: float, day: dt.date, **kwargs) -> BrokerTrade:
    return BrokerTrade(
        trade_id=f"O{next(_ids)}-T",
        symbol=kwargs.pop("trading_symbol", ""),  # history: no trading symbol, only the ISIN
        isin=kwargs.pop("isin", STOCKS[symbol]),
        side=side,
        product=kwargs.pop("product", "CNC"),
        quantity=qty,
        price=price,
        time=dt.datetime.combine(day, dt.time(11, 0), tzinfo=IST),
        charges=kwargs.pop("charges", 0.0),
    )


@dataclass
class Harness:
    sync: DhanSyncService
    broker: FakeBroker
    risk: RiskService
    credentials: BrokerCredentialsStore
    db: Database
    calendar: FixedCalendar


@pytest.fixture
def harness(db: Database, tmp_path: Path):
    equities = tmp_path / "equities.json"
    equities.write_text(
        json.dumps(
            [
                {"instrument_key": _key(s), "trading_symbol": s, "name": s, "isin": isin}
                for s, isin in STOCKS.items()
            ]
        )
    )
    instruments = InstrumentService(equities, tmp_path / "indices.json")
    calendar = FixedCalendar(NOW)
    candles = CandleService(FakeHistory(), CandleRepository(db), calendar)
    securities = SecuritiesService(
        FakeSecuritiesSource(),
        SecuritiesRepository(db),
        FundamentalsService(FakeFundamentals(), FundamentalsRepository(db), backfill_interval=0),
        calendar,
    )
    settings = RiskSettingsService(AppStateRepository(db))
    settings.save(RiskSettings(capital=1_000_000, broker_history_days=30))
    positions = PositionRepository(db)
    risk = RiskService(
        positions,
        settings,
        instruments=instruments,
        candles=candles,
        securities=securities,
        calendar=calendar,
        hub=MarketDataHub(FakeFeedFactory()),
        quotes=FakeQuotes(),
    )
    broker = FakeBroker(valid_until=NOW + dt.timedelta(hours=23, minutes=30))
    credentials = BrokerCredentialsStore(
        tmp_path / ".env", "DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", persist=False
    )
    sync = DhanSyncService(
        broker,
        credentials,
        trades=BrokerTradeRepository(db),
        positions=positions,
        state=AppStateRepository(db),
        instruments=instruments,
        calendar=calendar,
        risk=risk,
    )
    yield Harness(sync, broker, risk, credentials, db, calendar)
    risk.close()


def _run(harness: Harness) -> None:
    assert harness.sync.sync()
    harness.sync.wait(5)
    assert not harness.sync.running


def _connect(harness: Harness) -> None:
    assert harness.sync.connect("1000000000", "token-1")
    harness.sync.wait(5)


def test_nothing_happens_until_connected(harness: Harness):
    assert not harness.sync.status.connected
    assert harness.sync.sync() is False
    assert harness.broker.calls == []


def test_first_sync_imports_holdings_history_and_todays_fills(harness: Harness):
    broker = harness.broker
    broker.history = [
        _fill(Side.BUY, "GRAPHITE", 5, 855.85, dt.date(2026, 9, 9), charges=5.2),
        _fill(Side.BUY, "HFCL", 10, 100.0, dt.date(2026, 9, 1)),
        _fill(Side.SELL, "HFCL", 10, 112.0, dt.date(2026, 9, 10), charges=14.0),
    ]
    broker.today = [_fill(Side.BUY, "TCS", 2, 3000.0, TODAY, trading_symbol="TCS", isin=None)]
    broker.holdings_list = [BrokerHolding("GRAPHITE", STOCKS["GRAPHITE"], 5, 855.85)]

    _connect(harness)

    status = harness.sync.status
    assert status.error is None and status.mismatches == ()
    assert status.account_name == "TEST USER" and status.last_sync == NOW
    # History from 30 days back (settings) up to yesterday.
    assert broker.history_ranges == [(dt.date(2026, 8, 16), dt.date(2026, 9, 14))]

    view = harness.risk.portfolio()
    open_rows = {row.position.symbol: row for row in view.open}
    assert set(open_rows) == {"GRAPHITE", "TCS"}
    graphite = open_rows["GRAPHITE"].position
    assert (graphite.quantity, graphite.entry, graphite.opened_on) == (
        5,
        855.85,
        dt.date(2026, 9, 9),
    )
    assert graphite.source == "dhan" and graphite.stop is None
    assert graphite.instrument_key == _key("GRAPHITE")
    assert Breach.NO_STOP in open_rows["GRAPHITE"].risk.breaches

    (closed,) = view.closed
    assert (closed.symbol, closed.quantity, closed.entry, closed.exit_price) == (
        "HFCL",
        10,
        100.0,
        112.0,
    )
    assert closed.net_realised_pnl == pytest.approx(120 - 14)


def test_resyncs_are_incremental_and_keep_the_users_stop(harness: Harness):
    broker = harness.broker
    broker.history = [_fill(Side.BUY, "GRAPHITE", 5, 855.85, dt.date(2026, 9, 9))]
    broker.holdings_list = [BrokerHolding("GRAPHITE", STOCKS["GRAPHITE"], 5, 855.85)]
    _connect(harness)

    (position,) = harness.risk.positions()
    harness.risk.update_position(position.id, quantity=5, entry=855.85, stop=760, note="SL set")

    # Next day: one more buy, and today's history call covers only the new day.
    harness.calendar.current = NOW + dt.timedelta(days=1)
    broker.valid_until = harness.calendar.current + dt.timedelta(hours=23)  # renewed elsewhere
    broker.history.append(_fill(Side.BUY, "GRAPHITE", 5, 790.0, TODAY))
    broker.holdings_list = [BrokerHolding("GRAPHITE", STOCKS["GRAPHITE"], 10, 822.9)]
    _run(harness)

    assert broker.history_ranges[-1] == (TODAY, TODAY)
    (position,) = harness.risk.positions()
    assert position.quantity == 10
    assert position.entry == pytest.approx((5 * 855.85 + 5 * 790) / 10)
    assert (position.stop, position.note) == (760, "SL set")


def test_selling_out_turns_the_open_row_into_a_closed_one(harness: Harness):
    broker = harness.broker
    broker.history = [_fill(Side.BUY, "TCS", 4, 3000.0, dt.date(2026, 9, 2))]
    broker.holdings_list = [BrokerHolding("TCS", STOCKS["TCS"], 4, 3000.0)]
    _connect(harness)
    assert len(harness.risk.portfolio().open) == 1

    broker.today = [_fill(Side.SELL, "TCS", 4, 3100.0, TODAY, trading_symbol="TCS", isin=None)]
    _run(harness)

    view = harness.risk.portfolio()
    assert view.open == ()
    assert [(p.symbol, p.exit_price, p.closed_on) for p in view.closed] == [("TCS", 3100.0, TODAY)]


def test_a_sized_plan_links_to_the_dhan_buy_and_shows_oversizing(harness: Harness):
    plan = harness.risk.open_position("TCS", 2, 3000.0, 2850.0, opened_on=dt.date(2026, 9, 11))
    broker = harness.broker
    broker.history = [_fill(Side.BUY, "TCS", 5, 3010.0, dt.date(2026, 9, 11))]
    broker.holdings_list = [BrokerHolding("TCS", STOCKS["TCS"], 5, 3010.0)]
    _connect(harness)

    (row,) = harness.risk.portfolio().open
    assert row.position.id == plan.id  # the same row, now from Dhan
    assert (row.position.source, row.position.quantity, row.position.entry) == ("dhan", 5, 3010.0)
    assert (row.position.planned_quantity, row.position.stop) == (2, 2850.0)
    assert Breach.OVERSIZED in row.risk.breaches


def test_hidden_rows_stay_hidden(harness: Harness):
    broker = harness.broker
    broker.history = [_fill(Side.BUY, "TCS", 4, 3000.0, dt.date(2026, 9, 2))]
    broker.holdings_list = [BrokerHolding("TCS", STOCKS["TCS"], 4, 3000.0)]
    _connect(harness)
    (position,) = harness.risk.positions()
    harness.risk.delete_position(position.id)
    _run(harness)
    assert harness.risk.positions() == []


def test_a_token_close_to_expiry_is_renewed_and_saved(harness: Harness):
    broker = harness.broker
    broker.valid_until = NOW + dt.timedelta(hours=2)
    broker.renewed_to = BrokerToken("token-2", NOW + dt.timedelta(hours=24))
    _connect(harness)

    assert "renew" in broker.calls
    assert harness.credentials.current() == BrokerCredentials("1000000000", "token-2")
    assert harness.sync.status.token_valid_until == NOW + dt.timedelta(hours=24)
    assert any("renewed" in e for e in harness.sync.events())


def test_a_rejected_token_asks_for_a_new_one(harness: Harness):
    harness.broker.rejected = True
    _connect(harness)
    status = harness.sync.status
    assert status.needs_token and "web.dhan.co" in (status.error or "")
    assert harness.sync.sync_if_due() is False  # no retry loop with a dead token

    harness.broker.rejected = False
    _connect(harness)  # pasting a new token clears it
    assert not harness.sync.status.needs_token


def test_automatic_sync_runs_once_a_day(harness: Harness):
    _connect(harness)
    calls = len(harness.broker.calls)
    assert harness.sync.sync_if_due() is False
    assert len(harness.broker.calls) == calls
    harness.calendar.current = NOW + dt.timedelta(days=1)
    harness.broker.valid_until = harness.calendar.current + dt.timedelta(hours=23)
    assert harness.sync.sync_if_due() is True
    harness.sync.wait(5)


def test_unknown_instruments_are_reported_not_imported(harness: Harness):
    harness.broker.history = [
        _fill(Side.BUY, "TCS", 1, 10.0, dt.date(2026, 9, 2), isin="INE000UNKNOWN")
    ]
    _connect(harness)
    assert harness.risk.positions() == []
    assert any("INE000UNKNOWN" in m for m in harness.sync.status.mismatches)
