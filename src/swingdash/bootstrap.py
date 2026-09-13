"""
Composition root: the one place that knows which concrete adapter backs each
service. Tests call `build_services` with fakes for the network seams.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from importlib.resources import files

from swingdash.adapters.nse.source import NseSecuritiesSource
from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.migrations import migrate
from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.baselines import BaselineRepository
from swingdash.adapters.storage.repos.candles import CandleRepository
from swingdash.adapters.storage.repos.fundamentals import FundamentalsRepository
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.adapters.storage.repos.sessions import SessionRepository
from swingdash.adapters.storage.repos.watchlists import WatchlistRepository
from swingdash.adapters.upstox.calendar import UpstoxCalendarSource
from swingdash.adapters.upstox.client import UpstoxClient
from swingdash.adapters.upstox.feed import UpstoxFeedTransport
from swingdash.adapters.upstox.fundamentals import UpstoxFundamentals
from swingdash.adapters.upstox.history import UpstoxHistory
from swingdash.services.calendar import CalendarService
from swingdash.services.candles import CandleService
from swingdash.services.container import Services
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.instruments import InstrumentService
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.ports import (
    CalendarSource,
    FeedFactory,
    FeedTransport,
    FundamentalsSource,
    HistorySource,
    OnConnection,
    OnStatus,
    OnTick,
    SecuritiesSource,
)
from swingdash.services.preferences import PreferencesService
from swingdash.services.rvol.baselines import BaselineService
from swingdash.services.securities import SecuritiesService
from swingdash.services.watchlists import WatchlistService
from swingdash.settings import Settings


def default_watchlist_symbols() -> list[str]:
    seed = files("swingdash").joinpath("resources/default_watchlist.json").read_text("utf-8")
    return list(json.loads(seed).get("symbols", []))


def build_services(
    settings: Settings,
    *,
    history: HistorySource | None = None,
    calendar_source: CalendarSource | None = None,
    fundamentals_source: FundamentalsSource | None = None,
    feed_factory: FeedFactory | None = None,
    securities_source: SecuritiesSource | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> Services:
    settings.paths.ensure()
    db = Database(settings.paths.database)
    migrate(db)

    client = UpstoxClient(settings.require_token)
    history = history or UpstoxHistory(client)

    calendar = CalendarService(
        calendar_source or UpstoxCalendarSource(client),
        SessionRepository(db),
        **({"clock": clock} if clock else {}),
    )

    watchlists = WatchlistService(
        WatchlistRepository(db), AppStateRepository(db), default_watchlist_symbols
    )
    watchlists.seed_on_first_run()

    def upstox_feed(
        on_tick: OnTick, on_status: OnStatus, on_connection: OnConnection
    ) -> FeedTransport:
        return UpstoxFeedTransport(client, on_tick, on_status, on_connection)

    instruments = InstrumentService(
        settings.paths.equity_instruments, settings.paths.index_instruments
    )
    fundamentals = FundamentalsService(
        fundamentals_source or UpstoxFundamentals(client), FundamentalsRepository(db)
    )

    return Services(
        settings=settings,
        db=db,
        calendar=calendar,
        instruments=instruments,
        watchlists=watchlists,
        history=history,
        candles=CandleService(history, CandleRepository(db), calendar),
        fundamentals=fundamentals,
        baselines=BaselineService(history, BaselineRepository(db), calendar),
        hub=MarketDataHub(feed_factory or upstox_feed),
        securities=SecuritiesService(
            securities_source or NseSecuritiesSource(),
            SecuritiesRepository(db),
            fundamentals,
            calendar,
            isin_lookup=instruments.find_isin,
        ),
        preferences=PreferencesService(AppStateRepository(db), settings.mswing_index_key),
    )
