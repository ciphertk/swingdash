"""
RiskService - position sizing and the open-risk tracker behind the Risk tab.

- Capital, risk per trade and limits come from RiskSettingsService; open
  positions are entered by the user (the Analytics Token can't read holdings)
  and live in SQLite.
- Prices: the live feed (one hub subscription for open positions plus the
  symbol being sized), else the last cached daily close.
- Stops by ATR / recent low and the liquidity check need daily candles: read
  from the cache, topped up once per session on a background thread (paced by
  the shared Upstox limiter).
- Pull, don't push: ticks store a price; the tab polls `portfolio()` and asks
  `size()` as the form changes.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from dataclasses import dataclass

from swingdash.adapters.storage.repos.positions import PositionRepository
from swingdash.domain.bars import DailyBar
from swingdash.domain.risk.checks import RiskWarning, TradeContext, trade_warnings
from swingdash.domain.risk.portfolio import PortfolioSummary, Position, summarise
from swingdash.domain.risk.sizing import RiskSpec, SizingInput, SizingResult, size_position
from swingdash.domain.risk.stops import RiskInputError, StopMethod, resolve_stop
from swingdash.domain.securities import NOT_UNDER_SURVEILLANCE, PriceBand, Surveillance
from swingdash.services.candles import CandleService
from swingdash.services.instruments import InstrumentService, InstrumentsUnavailableError
from swingdash.services.market_data_hub import MarketDataHub, Subscription
from swingdash.services.ports import MarketCalendar
from swingdash.services.risk_settings import RiskSettings, RiskSettingsService
from swingdash.services.securities import SecuritiesService

logger = logging.getLogger(__name__)

# Sessions of history the checks need: 20-day average volume.
_VOLUME_SESSIONS = 20
_EVENT_LOG_SIZE = 20


@dataclass(frozen=True)
class Quote:
    price: float
    live: bool  # from the feed; otherwise the last cached daily close
    as_of: str | None = None  # the close's date, when not live


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    instrument_key: str
    quote: Quote | None
    bars: tuple[DailyBar, ...]
    history_pending: bool  # candles still being fetched
    band: PriceBand | None
    surveillance: Surveillance
    series: str | None
    lot_size: int
    avg_volume: float | None


@dataclass(frozen=True)
class SizedTrade:
    info: SymbolInfo
    stop: float
    result: SizingResult
    warnings: tuple[RiskWarning, ...]


@dataclass(frozen=True)
class PositionRow:
    position: Position
    quote: Quote | None
    days_held: int


@dataclass(frozen=True)
class PortfolioView:
    summary: PortfolioSummary
    open: tuple[PositionRow, ...]
    closed: tuple[Position, ...]


class RiskService:
    def __init__(
        self,
        repo: PositionRepository,
        settings: RiskSettingsService,
        *,
        instruments: InstrumentService,
        candles: CandleService,
        securities: SecuritiesService,
        calendar: MarketCalendar,
        hub: MarketDataHub,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._instruments = instruments
        self._candles = candles
        self._securities = securities
        self._calendar = calendar
        self._hub = hub

        self.version = 0  # bumped when positions, settings or fetched history change
        self._positions: list[Position] | None = None
        self._prices: dict[str, float] = {}  # written by the feed thread
        self._watched_key: str | None = None
        self._subscription: Subscription | None = None
        self._subscribed: frozenset[str] = frozenset()

        self._lock = threading.Lock()
        self._fetching: set[str] = set()
        self._attempted: set[tuple[str, dt.date]] = set()
        self._bars: dict[str, tuple[DailyBar, ...]] = {}  # cached candles, for today
        self._bars_day: dt.date | None = None
        self._events: deque[str] = deque(maxlen=_EVENT_LOG_SIZE)
        self._flags: dict[str, tuple[PriceBand | None, Surveillance]] = {}
        self._flags_version = -1

    # --- settings ----------------------------------------------------------

    @property
    def settings(self) -> RiskSettings:
        return self._settings.get()

    def save_settings(self, settings: RiskSettings) -> None:
        self._settings.save(settings)
        self._bars.clear()  # the history window depends on the settings
        self.version += 1

    # --- symbols -----------------------------------------------------------

    def symbols(self) -> list[str]:
        """Every NSE stock symbol, for autocomplete."""
        try:
            return sorted(e["trading_symbol"] for e in self._instruments.list_equities())
        except InstrumentsUnavailableError:
            return []

    def watch(self, symbol: str | None) -> None:
        """The symbol being sized: stream its price and top up its history."""
        key = self._key(symbol) if symbol else None
        if key == self._watched_key:
            return
        self._watched_key = key
        self._resubscribe()

    def info(self, symbol: str) -> SymbolInfo | None:
        """None if it isn't an NSE stock swingdash knows."""
        key = self._key(symbol)
        if key is None:
            return None
        symbol = symbol.strip().upper()
        bars = self._history(key)
        band, surveillance = self._flags_for(symbol)
        recent = bars[-_VOLUME_SESSIONS:]
        return SymbolInfo(
            symbol=symbol,
            instrument_key=key,
            quote=self._quote(key, bars),
            bars=bars,
            history_pending=key in self._fetching,
            band=band,
            surveillance=surveillance,
            series=self._instruments.find_series(symbol),
            lot_size=self._instruments.lot_size(symbol),
            avg_volume=sum(b.volume for b in recent) / len(recent) if recent else None,
        )

    # --- sizing ------------------------------------------------------------

    def size(
        self,
        symbol: str,
        entry: float,
        stop_method: StopMethod,
        stop_value: float,
        risk: RiskSpec | None = None,
    ) -> SizedTrade:
        """Raises RiskInputError with a message fit to show the user."""
        info = self.info(symbol)
        if info is None:
            raise RiskInputError(f"'{symbol.strip().upper()}' isn't an NSE stock swingdash knows.")
        settings = self.settings
        stop = resolve_stop(
            stop_method, entry, stop_value, info.bars, atr_period=settings.atr_period
        )
        summary = self.portfolio().summary
        result = size_position(
            SizingInput(
                capital=settings.capital,
                risk=risk or settings.risk,
                entry=entry,
                stop=stop,
                max_allocation_pct=settings.max_allocation_pct,
                free_capital=summary.free_capital,
                heat_left=summary.heat_left,
                lot_size=info.lot_size,
            )
        )
        context = TradeContext(
            band=info.band,
            surveillance=info.surveillance,
            series=info.series,
            avg_volume=info.avg_volume,
            liquidity_warn_pct=settings.liquidity_warn_pct,
        )
        return SizedTrade(info, stop, result, tuple(trade_warnings(result, context)))

    # --- positions ---------------------------------------------------------

    def positions(self) -> list[Position]:
        if self._positions is None:
            self._positions = self._repo.all()
        return list(self._positions)

    def portfolio(self) -> PortfolioView:
        positions = self.positions()
        today = self._calendar.today()
        rows = tuple(
            PositionRow(
                position=p,
                quote=self._quote(p.instrument_key, self._history(p.instrument_key))
                if p.instrument_key
                else None,
                days_held=(today - p.opened_on).days,
            )
            for p in positions
            if p.is_open
        )
        settings = self.settings
        return PortfolioView(
            summary=summarise(positions, settings.capital, settings.max_heat_pct),
            open=rows,
            closed=tuple(p for p in positions if not p.is_open),
        )

    def open_position(
        self, symbol: str, quantity: int, entry: float, stop: float, note: str = ""
    ) -> Position:
        key = self._key(symbol)
        if key is None:
            raise RiskInputError(f"'{symbol.strip().upper()}' isn't an NSE stock swingdash knows.")
        _check_position(quantity, entry, stop)
        if stop >= entry:
            raise RiskInputError("The stop must be below entry when opening a position.")
        position_id = self._repo.add(
            symbol.strip().upper(), key, quantity, entry, stop, self._calendar.today(), note
        )
        self._changed()
        position = self._repo.get(position_id)
        assert position is not None
        return position

    def update_position(
        self, position_id: int, *, quantity: int, entry: float, stop: float, note: str = ""
    ) -> None:
        """A stop at or above entry is fine here - that's a trailed stop."""
        _check_position(quantity, entry, stop)
        self._repo.update(position_id, quantity=quantity, entry=entry, stop=stop, note=note)
        self._changed()

    def close_position(
        self, position_id: int, exit_price: float, closed_on: dt.date | None = None
    ) -> None:
        if exit_price <= 0:
            raise RiskInputError("The exit price must be above zero.")
        self._repo.close(position_id, exit_price, closed_on or self._calendar.today())
        self._changed()

    def delete_position(self, position_id: int) -> None:
        self._repo.delete(position_id)
        self._changed()

    # --- lifecycle ---------------------------------------------------------

    def events(self) -> list[str]:
        drained = list(self._events)
        self._events.clear()
        return drained

    def close(self) -> None:
        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None

    # --- internals ---------------------------------------------------------

    def _changed(self) -> None:
        self._positions = None
        self.version += 1
        self._resubscribe()

    def _key(self, symbol: str | None) -> str | None:
        if not symbol or not symbol.strip():
            return None
        try:
            return self._instruments.find_instrument_key(symbol)
        except InstrumentsUnavailableError:
            return None

    def _resubscribe(self) -> None:
        keys = {p.instrument_key for p in self.positions() if p.is_open and p.instrument_key}
        if self._watched_key:
            keys.add(self._watched_key)
        wanted = frozenset(keys)
        if wanted == self._subscribed:
            return
        for key in wanted - self._subscribed:
            self._ensure_history(key)
        try:
            if not wanted:
                if self._subscription is not None:
                    self._subscription.close()
                    self._subscription = None
            elif self._subscription is None:
                self._subscription = self._hub.subscribe(sorted(wanted), self._on_tick)
            else:
                self._subscription.update(sorted(wanted))
        except Exception as exc:  # e.g. the feed's instrument cap
            logger.warning("risk price subscription failed", exc_info=True)
            self._events.append(f"live prices unavailable: {exc}")
            return
        self._subscribed = wanted

    def _on_tick(
        self, key: str, vtt: int | None, ltp: float | None, prev_close: float | None
    ) -> None:
        if ltp is not None and ltp > 0:
            self._prices[key] = ltp

    def _quote(self, key: str, bars: tuple[DailyBar, ...]) -> Quote | None:
        live = self._prices.get(key)
        if live is not None:
            return Quote(live, live=True)
        if bars:
            return Quote(bars[-1].close, live=False, as_of=bars[-1].date)
        return None

    def _lookback_days(self) -> int:
        settings = self.settings
        sessions = max(settings.atr_period + 1, settings.low_sessions, _VOLUME_SESSIONS + 1)
        return int(sessions * 1.6) + 15  # calendar days: weekends and holidays

    def _history(self, key: str) -> tuple[DailyBar, ...]:
        today = self._calendar.today()
        if today != self._bars_day:
            self._bars, self._bars_day = {}, today
        bars = self._bars.get(key)
        if bars is None:
            bars = tuple(self._candles.cached(key, self._lookback_days()))
            self._bars[key] = bars
        return bars

    def _ensure_history(self, key: str) -> None:
        today = self._calendar.today()
        with self._lock:
            if key in self._fetching or (key, today) in self._attempted:
                return
            self._attempted.add((key, today))
        if self._candles.is_current(key):
            return
        with self._lock:
            self._fetching.add(key)
        threading.Thread(
            target=self._fetch_history, args=(key,), name="risk-history", daemon=True
        ).start()

    def _fetch_history(self, key: str) -> None:
        try:
            self._candles.daily(key, self._lookback_days())
        except Exception as exc:
            logger.warning("risk history fetch failed for %s", key, exc_info=True)
            self._events.append(f"history for {key.partition('|')[2] or key} failed: {exc}")
        finally:
            with self._lock:
                self._fetching.discard(key)
            self._bars.pop(key, None)
            self.version += 1

    def _flags_for(self, symbol: str) -> tuple[PriceBand | None, Surveillance]:
        snapshot = self._securities.snapshot()
        if snapshot.version != self._flags_version:
            flags: dict[str, tuple[PriceBand | None, Surveillance]] = {
                e.symbol: (e.band, NOT_UNDER_SURVEILLANCE) for e in snapshot.etfs
            }
            flags.update({e.symbol: (e.band, e.surveillance) for e in snapshot.equities})
            self._flags, self._flags_version = flags, snapshot.version
        return self._flags.get(symbol, (None, NOT_UNDER_SURVEILLANCE))


def _check_position(quantity: int, entry: float, stop: float) -> None:
    if quantity <= 0:
        raise RiskInputError("Quantity must be at least 1.")
    if entry <= 0 or stop <= 0:
        raise RiskInputError("Entry and stop must be above zero.")
