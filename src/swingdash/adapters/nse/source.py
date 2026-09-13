"""NseSecuritiesSource - NSE's reference datasets behind the SecuritiesSource port."""

from __future__ import annotations

import datetime as dt
import logging

from swingdash.adapters.nse import parsers
from swingdash.adapters.nse.http import API, ARCHIVES, NseHttp, NseNotFoundError
from swingdash.domain.calendar import MAX_LOOKBACK_DAYS
from swingdash.domain.securities import (
    BandEntry,
    Etf,
    Fetched,
    IndexRow,
    ListedEquity,
    Surveillance,
)

logger = logging.getLogger(__name__)


class NseSecuritiesSource:
    def __init__(self, http: NseHttp | None = None) -> None:
        self._http = http or NseHttp()

    def equity_list(self) -> Fetched[list[ListedEquity]]:
        text, modified = self._http.text(f"{ARCHIVES}/equities/EQUITY_L.csv")
        return Fetched(parsers.parse_equity_list(text), modified)

    def price_bands(self) -> Fetched[list[BandEntry]]:
        text, modified = self._http.text(f"{ARCHIVES}/equities/sec_list.csv")
        return Fetched(parsers.parse_price_bands(text), modified)

    def etf_list(self) -> Fetched[list[Etf]]:
        text, modified = self._http.text(f"{ARCHIVES}/equities/eq_etfseclist.csv")
        return Fetched(parsers.parse_etf_list(text), modified)

    def indices(self) -> Fetched[list[IndexRow]]:
        rows, as_of = parsers.parse_all_indices(self._http.json(f"{API}/allIndices"))
        return Fetched(rows, as_of)

    def surveillance(self, today: dt.date) -> Fetched[dict[str, Surveillance]]:
        """
        The report endpoints first: they already list the next session's
        stages. If any of them fails, fall back to the newest daily
        regulatory-indicator file, which is a day behind but complete.
        Never a mix of the two.
        """
        try:
            stages, as_of = parsers.parse_surveillance_reports(
                self._http.json(f"{API}/reportASM"),
                self._http.json(f"{API}/reportGSM"),
                self._http.json(f"{API}/reportESM"),
            )
            return Fetched(stages, as_of)
        except Exception:
            logger.warning(
                "NSE surveillance reports unavailable, using the daily file", exc_info=True
            )

        day = today
        for _ in range(MAX_LOOKBACK_DAYS):
            try:
                text, _modified = self._http.text(f"{ARCHIVES}/cm/REG1_IND{day:%d%m%y}.csv")
            except NseNotFoundError:
                day -= dt.timedelta(days=1)
                continue
            return Fetched(parsers.parse_surveillance_file(text), day)
        raise NseNotFoundError(f"no surveillance file in the {MAX_LOOKBACK_DAYS} days to {today}")

    def close(self) -> None:
        self._http.close()
