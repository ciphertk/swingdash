"""
SecuritiesService - the Securities tab's data: every EQ stock, ETF and index
with price bands, surveillance stages and (from Upstox) sector and market cap.

Refreshing is manual and runs on one background thread in two phases:

1. NSE's five end-of-day datasets, a few seconds. Each is independent: one
   failing keeps its previous copy and records the error.
2. The sector backfill from Upstox fundamentals, paced to the rate limit -
   minutes on a first run, near-instant once cached.

The UI polls `snapshot()` and `progress`; this thread never calls into it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import threading
from collections.abc import Callable, Sized
from dataclasses import dataclass

from swingdash.adapters.storage.repos import securities as datasets
from swingdash.adapters.storage.repos.securities import SecuritiesRepository
from swingdash.domain.calendar import latest_publish_cutoff
from swingdash.domain.securities import (
    EMPTY_SNAPSHOT,
    NOT_UNDER_SURVEILLANCE,
    DatasetStatus,
    Equity,
    Fetched,
    ListedEquity,
    SecuritiesSnapshot,
)
from swingdash.services.fundamentals import FundamentalsService
from swingdash.services.ports import MarketCalendar, SecuritiesSource

logger = logging.getLogger(__name__)

# Rebuild the snapshot this often during the backfill, so sectors appear as
# they arrive rather than all at the end.
_REBUILD_EVERY_SECTORS = 50


@dataclass(frozen=True)
class RefreshProgress:
    running: bool = False
    phase: str = ""
    sectors_done: int = 0
    sectors_total: int = 0
    error: str | None = None


class SecuritiesService:
    def __init__(
        self,
        source: SecuritiesSource,
        repo: SecuritiesRepository,
        fundamentals: FundamentalsService,
        calendar: MarketCalendar,
        isin_lookup: Callable[[str], str | None] = lambda _symbol: None,
    ) -> None:
        self._source = source
        self._repo = repo
        self._fundamentals = fundamentals
        self._calendar = calendar
        self._isin_lookup = isin_lookup
        self._snapshot: SecuritiesSnapshot | None = None
        self._version = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        # Written only by the refresh thread; rebound whole, so reads are safe.
        self.progress = RefreshProgress()
        self._rebuild_lock = threading.Lock()
        self._stores: dict[str, Callable[[dt.datetime], None]] = {
            datasets.LISTINGS: self._store_listings,
            datasets.BANDS: self._store_bands,
            datasets.SURVEILLANCE: self._store_surveillance,
            datasets.ETFS: self._store_etfs,
            datasets.INDICES: self._store_indices,
        }

    # --- reads --------------------------------------------------------------

    def snapshot(self) -> SecuritiesSnapshot:
        if self._snapshot is None:
            self._rebuild()
        return self._snapshot or EMPTY_SNAPSHOT

    @property
    def refreshing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def newer_data_likely(self) -> bool:
        """True when NSE has probably published files since the last fetch. A hint only."""
        fetched = [s.fetched_at for s in self.snapshot().datasets.values() if s.fetched_at]
        if not fetched:
            return True
        cutoff = latest_publish_cutoff(self._calendar.now(), self._calendar.get_session)
        return cutoff is not None and min(fetched) < cutoff

    # --- refresh ------------------------------------------------------------

    def refresh(self, include_sectors: bool = True) -> bool:
        """Start a background refresh. False if one is already running."""
        with self._lock:
            if self.refreshing:
                return False
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run_logged,
                args=(include_sectors, None),
                name="securities-refresh",
                daemon=True,
            )
            self._thread.start()
            return True

    def refresh_blocking(
        self,
        include_sectors: bool = True,
        on_progress: Callable[[RefreshProgress], None] | None = None,
    ) -> RefreshProgress:
        self._cancel.clear()
        self._run(include_sectors, on_progress)
        return self.progress

    def cancel(self, timeout: float = 5.0) -> None:
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def close(self) -> None:
        self.cancel()
        self._source.close()

    def _run_logged(
        self, include_sectors: bool, on_progress: Callable[[RefreshProgress], None] | None
    ) -> None:
        try:
            self._run(include_sectors, on_progress)
        except Exception as exc:
            logger.exception("securities refresh failed")
            self.progress = RefreshProgress(error=f"{type(exc).__name__}: {exc}")

    def _run(
        self, include_sectors: bool, on_progress: Callable[[RefreshProgress], None] | None
    ) -> None:
        def report(progress: RefreshProgress) -> None:
            self.progress = progress
            if on_progress:
                on_progress(progress)

        errors: list[str] = []
        for name in datasets.DATASETS:
            if self._cancel.is_set():
                break
            report(RefreshProgress(running=True, phase=f"fetching {name}"))
            error = self._fetch(name)
            if error:
                errors.append(f"{name}: {error}")
        self._rebuild()

        summary = "; ".join(errors) or None
        if include_sectors and not self._cancel.is_set():
            isins = [e.isin for e in self.snapshot().equities if e.isin]
            last_rebuild = 0

            def sector_progress(done: int, total: int) -> None:
                nonlocal last_rebuild
                report(
                    RefreshProgress(
                        running=True,
                        phase="sectors",
                        sectors_done=done,
                        sectors_total=total,
                        error=summary,
                    )
                )
                if done - last_rebuild >= _REBUILD_EVERY_SECTORS:
                    last_rebuild = done
                    self._rebuild()

            result = self._fundamentals.backfill(
                isins, cancel=self._cancel, on_progress=sector_progress
            )
            if result.gave_up:
                errors.append(f"sectors: stopped after repeated failures ({result.failed} failed)")
            self._rebuild()
            done = result.fetched + result.failed
            report(
                RefreshProgress(
                    sectors_done=done, sectors_total=result.total, error="; ".join(errors) or None
                )
            )
            return

        report(RefreshProgress(error=summary))

    def _fetch(self, name: str) -> str | None:
        """Fetch and store one dataset; the error message if it failed."""
        now = self._calendar.now()
        try:
            self._stores[name](now)
        except Exception as exc:
            logger.warning("securities dataset %s failed", name, exc_info=True)
            message = f"{type(exc).__name__}: {exc}"
            self._repo.record_error(name, message)
            return message
        return None

    def _store_listings(self, now: dt.datetime) -> None:
        fetched = self._source.equity_list()
        self._repo.replace_listings(fetched.rows, _status(datasets.LISTINGS, fetched, now))

    def _store_bands(self, now: dt.datetime) -> None:
        fetched = self._source.price_bands()
        self._repo.replace_bands(fetched.rows, _status(datasets.BANDS, fetched, now))

    def _store_surveillance(self, now: dt.datetime) -> None:
        fetched = self._source.surveillance(now.date())
        self._repo.replace_surveillance(fetched.rows, _status(datasets.SURVEILLANCE, fetched, now))

    def _store_etfs(self, now: dt.datetime) -> None:
        fetched = self._source.etf_list()
        self._repo.replace_etfs(fetched.rows, _status(datasets.ETFS, fetched, now))

    def _store_indices(self, now: dt.datetime) -> None:
        fetched = self._source.indices()
        self._repo.replace_indices(fetched.rows, _status(datasets.INDICES, fetched, now))

    # --- join ---------------------------------------------------------------

    def _rebuild(self) -> None:
        with self._rebuild_lock:
            self._snapshot = self._join(self._version + 1)
            self._version += 1

    def _join(self, version: int) -> SecuritiesSnapshot:
        """
        Join the stored datasets into one snapshot. The price band list
        decides which securities are EQ (the equity list's series lags a
        day); ETFs trade as EQ too, so they are split out by the ETF list.
        """
        bands = self._repo.bands()
        band_by_symbol = {b.symbol: b for b in bands}
        listings = {listing.symbol: listing for listing in self._repo.listings()}
        stages = self._repo.surveillance()
        etfs = self._repo.etfs()
        etf_symbols = {e.symbol for e in etfs}

        stocks = [b for b in bands if b.symbol not in etf_symbols]
        isins = {b.symbol: self._isin_for(b.symbol, listings) for b in stocks}
        profiles = self._fundamentals.cached_profiles(i for i in isins.values() if i)

        equities: list[Equity] = []
        for band in stocks:
            listing = listings.get(band.symbol)
            isin = isins[band.symbol]
            profile = profiles.get(isin) if isin else None
            equities.append(
                Equity(
                    symbol=band.symbol,
                    name=listing.name if listing else band.name,
                    isin=isin,
                    listed_on=listing.listed_on if listing else None,
                    band=band.band,
                    surveillance=stages.get(band.symbol, NOT_UNDER_SURVEILLANCE),
                    sector=profile.sector if profile else None,
                    market_cap_cr=profile.market_cap_cr if profile else None,
                )
            )

        joined_etfs = tuple(
            dataclasses.replace(
                etf,
                name=band_by_symbol[etf.symbol].name if etf.symbol in band_by_symbol else etf.name,
                band=band_by_symbol[etf.symbol].band if etf.symbol in band_by_symbol else None,
            )
            for etf in etfs
        )

        return SecuritiesSnapshot(
            equities=tuple(equities),
            etfs=joined_etfs,
            indices=tuple(self._repo.indices()),
            datasets=self._repo.statuses(),
            version=version,
        )

    def _isin_for(self, symbol: str, listings: dict[str, ListedEquity]) -> str | None:
        listing = listings.get(symbol)
        if listing and listing.isin:
            return listing.isin
        try:
            return self._isin_lookup(symbol)
        except Exception:
            return None


def _status(name: str, fetched: Fetched[Sized], fetched_at: dt.datetime) -> DatasetStatus:
    return DatasetStatus(
        name=name, as_of=fetched.as_of, fetched_at=fetched_at, rows=len(fetched.rows)
    )
