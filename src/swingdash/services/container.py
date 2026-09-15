"""
The application's service container: every long-lived service, built once
by `swingdash.bootstrap.build_services` and handed to the UI.

UI code reaches data only through this object - never by importing
adapters - which is what lets tests swap in fakes for everything that
touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass

from swingdash.adapters.storage.db import Database
from swingdash.services.calendar import CalendarService
from swingdash.services.candles import CandleService
from swingdash.services.chartink import ChartinkService
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.instruments import InstrumentService
from swingdash.services.market_data_hub import MarketDataHub
from swingdash.services.ports import HistorySource
from swingdash.services.preferences import PreferencesService
from swingdash.services.risk import RiskService
from swingdash.services.rvol.baselines import BaselineService
from swingdash.services.rvol.engine import RvolEngine
from swingdash.services.scanner.engine import ScannerEngine
from swingdash.services.securities import SecuritiesService
from swingdash.services.watchlists import WatchlistService
from swingdash.settings import Settings


@dataclass(frozen=True)
class Services:
    settings: Settings
    db: Database
    calendar: CalendarService
    instruments: InstrumentService
    watchlists: WatchlistService
    history: HistorySource
    candles: CandleService
    fundamentals: FundamentalsService
    baselines: BaselineService
    hub: MarketDataHub
    securities: SecuritiesService
    preferences: PreferencesService
    chartink: ChartinkService
    risk: RiskService

    def new_rvol_engine(self, symbols: list[str]) -> RvolEngine:
        return RvolEngine(
            symbols,
            calendar=self.calendar,
            baselines=self.baselines,
            resolve_key=self.instruments.find_instrument_key,
            hub=self.hub,
        )

    def new_scanner_engine(self, symbols: list[str]) -> ScannerEngine:
        index_key = self.preferences.scanner_index()
        return ScannerEngine(
            symbols,
            calendar=self.calendar,
            candles=self.candles,
            resolve_key=self.instruments.find_instrument_key,
            hub=self.hub,
            index_key=index_key,
            index_name=self.instruments.index_name(index_key),
        )

    def close(self) -> None:
        # Background work first, so nothing is mid-write when the DB closes.
        self.securities.close()
        self.chartink.close()
        self.risk.close()
        self.hub.close()
        self.db.close()
