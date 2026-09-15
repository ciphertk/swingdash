"""
ChartinkService - saved Chartink screeners/widgets, running them, and adding
swingdash's own columns to what comes back.

- Saved items and their last results live in SQLite, so reopening the tab
  shows results without asking Chartink again.
- Runs are on demand, one at a time on a background thread (a dashboard's
  widgets queue up behind each other); the UI polls `status`/`version`.
- For stock lists, each row gets its NSE price band (from the Securities
  tab's data) and Burst Power (from local daily candles via
  DailyContextLoader - symbols not cached fetch history once, paced).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from swingdash.adapters.storage.repos.chartink import ChartinkRepository
from swingdash.domain.calendar import Session
from swingdash.domain.chartink import (
    ChartinkInputError,
    ChartinkItem,
    ChartinkKind,
    ChartinkRequest,
    ChartinkRow,
    ColumnSpec,
    DashboardDef,
    ImportTarget,
    ScreenerDef,
    WidgetDef,
    parse_user_input,
)
from swingdash.domain.metrics.burst_score import BurstScoreResult
from swingdash.domain.scanner import SymbolContext, TodayBar, live_symbol
from swingdash.domain.securities import PriceBand
from swingdash.services.candles import CandleService
from swingdash.services.daily_contexts import ContextJob, DailyContextLoader, session_before
from swingdash.services.instruments import InstrumentService, InstrumentsUnavailableError
from swingdash.services.ports import ChartinkSourcePort, MarketCalendar
from swingdash.services.securities import SecuritiesService

logger = logging.getLogger(__name__)

# How long a looked-up trading session stays fresh (it only changes at the open).
_SESSION_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class EnrichedRow:
    row: ChartinkRow
    symbol: str | None  # the NSE symbol, when the row's key is one
    band: PriceBand | None
    burst: BurstScoreResult | None


@dataclass(frozen=True)
class ChartinkView:
    item: ChartinkItem
    rows: tuple[EnrichedRow, ...]
    # Our extra columns apply (the result is a stock list).
    enriched: bool
    bands_available: bool  # Securities data has been fetched at least once
    history_pending: bool  # Burst Power still filling in for some symbols


@dataclass(frozen=True)
class RunStatus:
    running_id: int | None
    queued: tuple[int, ...]

    @property
    def busy(self) -> bool:
        return self.running_id is not None or bool(self.queued)


class ChartinkService:
    def __init__(
        self,
        source: ChartinkSourcePort,
        repo: ChartinkRepository,
        *,
        instruments: InstrumentService,
        securities: SecuritiesService,
        candles: CandleService,
        calendar: MarketCalendar,
    ) -> None:
        self._source = source
        self._repo = repo
        self._instruments = instruments
        self._securities = securities
        self._calendar = calendar
        self._loader = DailyContextLoader(candles, workers=4, name="chartink")

        self._items: list[ChartinkItem] | None = None
        self.version = 0  # bumped whenever items or results change

        self._queue: deque[int] = deque()
        self._running_id: int | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._runner: threading.Thread | None = None

        self._contexts: dict[str, SymbolContext | None] = {}
        self._requested: set[str] = set()
        self._context_session: dt.date | None = None
        self._bands: dict[str, PriceBand | None] = {}
        self._bands_version = -1
        self._session_cache: tuple[float, Session | None, dt.date | None] = (
            float("-inf"),
            None,
            None,
        )

    # --- saved items -----------------------------------------------------------

    def items(self) -> list[ChartinkItem]:
        if self._items is None:
            self._items = self._repo.all()
        return list(self._items)

    def get(self, item_id: int) -> ChartinkItem | None:
        return next((item for item in self.items() if item.id == item_id), None)

    @staticmethod
    def parse(text: str) -> ChartinkRequest | ImportTarget:
        """Raises ChartinkInputError with a message fit to show the user."""
        return parse_user_input(text)

    def add(
        self,
        name: str,
        request: ChartinkRequest,
        source_url: str | None = None,
        columns: dict[str, ColumnSpec] | None = None,
    ) -> ChartinkItem:
        item_id = self._repo.add(name.strip() or "Untitled", request, source_url, None, columns)
        self._changed()
        item = self.get(item_id)
        assert item is not None
        return item

    def fetch_screener(self, url: str) -> ScreenerDef:
        """Network call - run it off the UI thread."""
        return self._source.screener(url)

    def fetch_dashboard(self, url: str) -> DashboardDef:
        """Network call - run it off the UI thread."""
        return self._source.dashboard(url)

    def add_screener(
        self,
        screener: ScreenerDef,
        url: str,
        name: str | None = None,
        payload: ChartinkRequest | None = None,
    ) -> ChartinkItem:
        """
        `payload` is the screener's request as copied from the browser; with
        it, the item runs exactly that (custom columns included) and takes
        its column names and colours from the page.
        """
        if payload is not None:
            if payload.kind is not ChartinkKind.SCREENER:
                raise ChartinkInputError("That payload is a widget's, not a screener's.")
            return self.add(name or screener.name, payload, url, screener.columns)
        if screener.is_private or not screener.clause:
            raise ChartinkInputError(
                f"'{screener.name}' is private or has no scan clause - paste its request "
                "payload from the browser's network tab instead."
            )
        return self.add(name or screener.name, screener.request(), url, screener.columns)

    def add_widgets(
        self, dashboard: DashboardDef, widgets: list[WidgetDef], url: str
    ) -> list[ChartinkItem]:
        """Imported together under the dashboard's name."""
        if not widgets:
            return []
        collection = self._unique_collection(dashboard.name)
        ids = self._repo.add_many([(w.name, w.request(), url, collection, {}) for w in widgets])
        self._changed()
        return [item for item in self.items() if item.id in set(ids)]

    def rename(self, item_id: int, name: str) -> None:
        self._repo.rename(item_id, name.strip() or "Untitled")
        self._changed()

    def rename_collection(self, old: str, new: str) -> str:
        """Returns the name actually used ("X (2)" if another dashboard is called X)."""
        wanted = new.strip() or old
        if wanted == old:
            return old
        name = self._unique_collection(wanted)
        self._repo.rename_collection(old, name)
        self._changed()
        return name

    def delete(self, item_id: int) -> None:
        self._repo.delete(item_id)
        self._changed()

    def delete_collection(self, collection: str) -> None:
        self._repo.delete_collection(collection)
        self._changed()

    def collection_items(self, collection: str) -> list[ChartinkItem]:
        return [item for item in self.items() if item.collection == collection]

    # --- running -----------------------------------------------------------------

    @property
    def status(self) -> RunStatus:
        return RunStatus(self._running_id, tuple(self._queue))

    def run(self, item_ids: list[int]) -> int:
        """Queue items to run one after another. Returns how many were newly queued."""
        with self._lock:
            added = 0
            for item_id in item_ids:
                if item_id != self._running_id and item_id not in self._queue:
                    self._queue.append(item_id)
                    added += 1
            if self._runner is None:
                self._runner = threading.Thread(
                    target=self._run_loop, name="chartink-run", daemon=True
                )
                self._runner.start()
        self._wake.set()
        self.version += 1
        return added

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                item_id = self._queue.popleft() if self._queue else None
                self._running_id = item_id
            if item_id is None:
                self._wake.clear()
                self._wake.wait(0.5)
                continue
            self.version += 1
            try:
                self._run_one(item_id)
            finally:
                with self._lock:
                    self._running_id = None
                self.version += 1

    def _run_one(self, item_id: int) -> None:
        item = self._repo.get(item_id)
        if item is None:
            return
        try:
            result = self._source.run(item.request.runnable(), referer=item.source_url)
        except Exception as exc:  # the adapter's errors carry user-facing messages
            logger.warning("chartink run failed for %s", item.name, exc_info=True)
            self._repo.save_error(item_id, str(exc) or type(exc).__name__)
        else:
            self._repo.save_result(item_id, result, self._calendar.now())
        self._changed()

    # --- enrichment ----------------------------------------------------------------

    def view(self, item_id: int) -> ChartinkView | None:
        item = self.get(item_id)
        if item is None:
            return None
        result = item.result
        if result is None:
            return ChartinkView(item, (), False, False, False)
        if not result.is_stock_list:
            rows = tuple(EnrichedRow(row, None, None, None) for row in result.rows)
            return ChartinkView(item, rows, False, False, False)

        today = self._today()
        bands = self._band_map()
        keys: dict[str, str] = {}
        for row in result.rows:
            key = self._instrument_key(row.key)
            if key is not None:
                keys[row.key] = key
        self._ensure_contexts(keys, today)

        enriched: list[EnrichedRow] = []
        for row in result.rows:
            key = keys.get(row.key)
            context = self._contexts.get(key) if key else None
            burst = None
            if context is not None and today is not None:
                close = row.values.get("close")
                bar = TodayBar(
                    today.date,
                    today.previous_session,
                    float(close) if isinstance(close, int | float) else None,
                    today.closed,
                )
                burst = live_symbol(context, bar).burst
            enriched.append(EnrichedRow(row, row.key if key else None, bands.get(row.key), burst))
        pending = any(k not in self._contexts for k in keys.values()) or self._loader.pending > 0
        return ChartinkView(item, tuple(enriched), True, bool(bands), pending)

    def symbols(self, item_id: int) -> list[str]:
        """NSE symbols in the item's last result, in order - for saving as a watchlist."""
        item = self.get(item_id)
        if item is None or item.result is None or not item.result.is_stock_list:
            return []
        seen: dict[str, None] = {}
        for row in item.result.rows:
            if row.key and self._instrument_key(row.key) is not None:
                seen.setdefault(row.key, None)
        return list(seen)

    def events(self) -> list[str]:
        return self._loader.events()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._loader.stop()
        self._source.close()

    # --- helpers -------------------------------------------------------------------

    def _changed(self) -> None:
        self._items = None
        self.version += 1

    def _unique_collection(self, name: str) -> str:
        taken = {item.collection for item in self.items() if item.collection}
        candidate, number = name, 2
        while candidate in taken:
            candidate, number = f"{name} ({number})", number + 1
        return candidate

    def _instrument_key(self, symbol: str) -> str | None:
        try:
            return self._instruments.find_instrument_key(symbol)
        except InstrumentsUnavailableError:
            return None

    def _band_map(self) -> dict[str, PriceBand | None]:
        snapshot = self._securities.snapshot()
        if snapshot.version != self._bands_version:
            bands: dict[str, PriceBand | None] = {e.symbol: e.band for e in snapshot.etfs}
            bands.update({e.symbol: e.band for e in snapshot.equities})
            self._bands, self._bands_version = bands, snapshot.version
        return self._bands

    def _today(self) -> TodayBar | None:
        """The active session as a TodayBar with no price. The lookup is cached for a minute."""
        now = self._calendar.now()
        checked_at, session, previous = self._session_cache
        if now.timestamp() - checked_at >= _SESSION_TTL_SECONDS:
            session = self._calendar.active_session(now)
            previous = session_before(self._calendar, session.date) if session else None
            self._session_cache = (now.timestamp(), session, previous)
        if session is None:
            return None
        return TodayBar(session.date, previous, None, now >= session.close_at)

    def _ensure_contexts(self, keys: dict[str, str], today: TodayBar | None) -> None:
        if today is None:
            return
        if self._context_session != today.date:
            # A new session: yesterday's contexts are stale.
            self._contexts, self._requested = {}, set()
            self._context_session = today.date
        missing = [(symbol, key) for symbol, key in keys.items() if key not in self._requested]
        if not missing:
            return
        self._requested.update(key for _, key in missing)
        session_date = today.date
        jobs = [
            ContextJob(key, symbol, self._deliver(key, session_date)) for symbol, key in missing
        ]
        self._loader.load_async(jobs, session_date)

    def _deliver(self, key: str, session_date: dt.date) -> Callable[[SymbolContext | None], None]:
        def deliver(context: SymbolContext | None) -> None:
            if self._context_session == session_date:
                self._contexts[key] = context
                self.version += 1

        return deliver
