"""
HTTP access to nseindia.com.

NSE's CDN rejects requests without a browser-like User-Agent. The archive
files and report endpoints used here need no cookies (verified Sep 2026);
`www.nseindia.com/api/quote-*` endpoints do and are deliberately not used.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from swingdash.domain.calendar import IST

logger = logging.getLogger(__name__)

ARCHIVES = "https://nsearchives.nseindia.com/content"
API = "https://www.nseindia.com/api"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


class NseUnavailableError(RuntimeError):
    pass


class NseNotFoundError(NseUnavailableError):
    """The file doesn't exist (yet) - e.g. a report for a date not published."""


class NseFormatError(ValueError):
    """NSE changed a file's layout; parsing it would produce wrong data."""


class NseHttp:
    def __init__(
        self,
        timeout: float = 30,
        retries: int = 2,
        backoff_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def text(self, url: str) -> tuple[str, dt.date | None]:
        """Body plus the IST date the file was last modified, when NSE says."""
        response = self._get(url)
        return response.text, _last_modified_date(response)

    def json(self, url: str) -> Any:
        response = self._get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise NseFormatError(f"{url} did not return JSON") from exc

    def _get(self, url: str) -> requests.Response:
        failure = "no attempt made"
        for attempt in range(self._retries + 1):
            try:
                response = self._session.get(url, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                failure = str(exc)
            else:
                if response.status_code == 200:
                    return response
                if response.status_code == 404:
                    raise NseNotFoundError(f"{url} not found")
                if response.status_code == 403:
                    # A block, not a blip - retrying only makes it stickier.
                    raise NseUnavailableError(f"NSE refused {url} (403)")
                failure = f"HTTP {response.status_code}"
            if attempt < self._retries:
                logger.info("NSE %s failed (%s), retrying", url, failure)
                self._sleep(self._backoff * (attempt + 1))
        raise NseUnavailableError(f"NSE {url} failed: {failure}")

    def close(self) -> None:
        self._session.close()


def _last_modified_date(response: requests.Response) -> dt.date | None:
    header = response.headers.get("Last-Modified")
    if not header:
        return None
    try:
        return email.utils.parsedate_to_datetime(header).astimezone(IST).date()
    except (TypeError, ValueError):
        return None
