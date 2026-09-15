"""
HTTP access to Dhan's Trading API v2 (https://api.dhan.co/v2/), read-only.

Every request carries the user's access token in an `access-token` header
(plus `client-id`). Tokens generated on web.dhan.co last 24 hours; an expired
or wrong one answers 401 / DH-901. Reading holdings, positions and trades
needs no static IP - that's only for placing orders, which swingdash never
does. Rate limits are per second (data 5/s, non-trading 20/s), so requests
go one at a time, at least MIN_INTERVAL_SECONDS apart.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import requests

from swingdash.domain.errors import BrokerAuthError, BrokerUnavailableError

BASE_URL = "https://api.dhan.co/v2"
MIN_INTERVAL_SECONDS = 0.5
TIMEOUT = (10.0, 30.0)
_AUTH_CODES = {"DH-901", "DH-902"}


@dataclass(frozen=True)
class DhanCredentials:
    client_id: str
    access_token: str = field(repr=False)


class DhanFormatError(ValueError):
    """Dhan answered, but not in the shape swingdash understands."""


class DhanHttp:
    def __init__(
        self,
        credentials: Callable[[], DhanCredentials | None],
        min_interval: float = MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        session: requests.Session | None = None,
    ) -> None:
        # A callable, so a renewed token is picked up without rebuilding this.
        self._credentials = credentials
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._session = session or requests.Session()
        self._last_request = float("-inf")
        self._lock = threading.Lock()

    def get(self, path: str, headers: Mapping[str, str] | None = None) -> Any:
        credentials = self._credentials()
        if credentials is None or not credentials.access_token:
            raise BrokerAuthError("Dhan isn't connected - paste an access token first.")
        request_headers = {
            "access-token": credentials.access_token,
            "client-id": credentials.client_id,
            "Accept": "application/json",
            **(headers or {}),
        }
        with self._lock:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last_request = self._clock()
            try:
                response = self._session.get(
                    f"{BASE_URL}/{path.lstrip('/')}", headers=request_headers, timeout=TIMEOUT
                )
            except requests.RequestException as exc:
                raise BrokerUnavailableError(f"Dhan couldn't be reached: {exc}") from exc
        return _payload(response)


def _payload(response: requests.Response) -> Any:
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    error = _error(body)
    if response.status_code == 401 or (error and error[0] in _AUTH_CODES):
        message = error[1] if error else "the access token was rejected"
        raise BrokerAuthError(f"Dhan: {message} - generate a new token on web.dhan.co.")
    if response.status_code == 429 or (error and error[0] == "DH-904"):
        raise BrokerUnavailableError("Dhan is rate limiting requests - try again in a minute.")
    if response.status_code >= 400 or error:
        detail = error[1] if error else f"HTTP {response.status_code}"
        raise BrokerUnavailableError(f"Dhan: {detail}")
    if body is None:
        raise DhanFormatError(
            f"Dhan returned something that isn't JSON (HTTP {response.status_code})"
        )
    return body


def _error(body: Any) -> tuple[str, str] | None:
    """Dhan errors look like {"errorType", "errorCode", "errorMessage"}."""
    if isinstance(body, dict) and body.get("errorCode"):
        code = str(body.get("errorCode"))
        return code, str(body.get("errorMessage") or code)
    return None
