"""
HTTP access to chartink.com.

Chartink's pages are a Laravel app behind Cloudflare. Its data endpoints take
a form POST with an `x-csrf-token` header matching the session; the token is
in every page's <meta name="csrf-token">. A missing or stale token gets a 419
{"message": "CSRF token mismatch."} (verified Sep 2026), so the token is
fetched lazily and refreshed once on a 419.

Deliberately gentle - this is someone else's website, not an API: one
request at a time and at least MIN_INTERVAL_SECONDS apart.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://chartink.com"
# Any Chartink page carries the token; the screener builder is a stable one.
TOKEN_PAGE = f"{BASE_URL}/screener/"
MIN_INTERVAL_SECONDS = 1.0
TIMEOUT = (10.0, 30.0)
_CSRF_MISMATCH = 419

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_TOKEN = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"')


class ChartinkUnavailableError(RuntimeError):
    pass


class ChartinkFormatError(ValueError):
    """Chartink answered, but not in the shape swingdash understands - likely a site change."""


def csrf_token(page: str) -> str | None:
    match = _TOKEN.search(page)
    return match.group(1) if match else None


class ChartinkHttp:
    def __init__(
        self,
        min_interval: float = MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        session: requests.Session | None = None,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update(_HEADERS)
        self._token: str | None = None
        self._last_request = float("-inf")
        self._lock = threading.Lock()

    def page(self, url: str) -> str:
        """A Chartink page's HTML. Also refreshes the CSRF token from it."""
        with self._lock:
            response = self._request("GET", url)
            if response.status_code == 404:
                raise ChartinkUnavailableError(f"{url} was not found on Chartink")
            self._raise_for_status(response, url)
            self._token = csrf_token(response.text) or self._token
            return response.text

    def post(self, path: str, data: dict[str, str], referer: str = TOKEN_PAGE) -> Any:
        """POST a form to a Chartink data endpoint; the decoded JSON body."""
        url = f"{BASE_URL}{path}"
        with self._lock:
            if self._token is None:
                self._refresh_token(referer)
            response = self._post(url, data, referer)
            if response.status_code == _CSRF_MISMATCH:
                logger.info("Chartink CSRF token expired, refreshing")
                self._refresh_token(referer)
                response = self._post(url, data, referer)
            self._raise_for_status(response, url)
            try:
                return response.json()
            except ValueError as exc:
                raise ChartinkFormatError(f"{url} did not return JSON") from exc

    def close(self) -> None:
        self._session.close()

    # --- lock held ------------------------------------------------------------

    def _refresh_token(self, referer: str) -> None:
        response = self._request("GET", referer)
        self._raise_for_status(response, referer)
        token = csrf_token(response.text)
        if token is None:
            raise ChartinkFormatError("Chartink page has no CSRF token - the site may have changed")
        self._token = token

    def _post(self, url: str, data: dict[str, str], referer: str) -> requests.Response:
        headers = {
            "x-csrf-token": self._token or "",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
        return self._request("POST", url, data=data, headers=headers)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        wait = self._last_request + self._min_interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        try:
            return self._session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise ChartinkUnavailableError(f"Couldn't reach Chartink: {exc}") from exc
        finally:
            self._last_request = self._clock()

    @staticmethod
    def _raise_for_status(response: requests.Response, url: str) -> None:
        if response.status_code == 200:
            return
        if response.status_code in (403, 429, 503):
            raise ChartinkUnavailableError(
                f"Chartink refused the request ({response.status_code}) - it may be rate "
                "limiting; try again in a few minutes"
            )
        raise ChartinkUnavailableError(f"Chartink {url} answered {response.status_code}")
