import datetime as dt

import pytest
import requests

from swingdash.adapters.nse.http import NseHttp, NseNotFoundError, NseUnavailableError


class _Response:
    def __init__(self, status: int, text: str = "", headers: dict[str, str] | None = None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def _http(responses: list[object]) -> tuple[NseHttp, list[str], list[float]]:
    calls: list[str] = []
    sleeps: list[float] = []
    http = NseHttp(retries=2, backoff_seconds=1.0, sleep=sleeps.append)

    def get(url: str, timeout: float):
        calls.append(url)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    http._session.get = get  # type: ignore[method-assign]
    return http, calls, sleeps


def test_transient_failures_are_retried_with_backoff():
    http, calls, sleeps = _http(
        [
            requests.ConnectionError("reset"),
            _Response(503),
            _Response(200, "ok", {"Last-Modified": "Fri, 11 Sep 2026 14:18:10 GMT"}),
        ]
    )
    text, modified = http.text("https://x/sec_list.csv")
    assert text == "ok"
    assert modified == dt.date(2026, 9, 11)  # 19:48 IST the same day
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_a_block_is_not_retried():
    http, calls, _ = _http([_Response(403)])
    with pytest.raises(NseUnavailableError):
        http.text("https://x/api")
    assert len(calls) == 1


def test_missing_file_is_distinguishable():
    http, _, _ = _http([_Response(404)])
    with pytest.raises(NseNotFoundError):
        http.text("https://x/REG1_IND150926.csv")


def test_gives_up_after_retries():
    http, calls, _ = _http([_Response(500), _Response(500), _Response(500)])
    with pytest.raises(NseUnavailableError, match="HTTP 500"):
        http.text("https://x/file.csv")
    assert len(calls) == 3
