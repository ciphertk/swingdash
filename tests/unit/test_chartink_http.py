"""ChartinkHttp: CSRF token handling and gentle pacing, with a fake session."""

from __future__ import annotations

import pytest
import requests

from swingdash.adapters.chartink.http import ChartinkHttp, ChartinkUnavailableError

PAGE = '<html><head><meta name="csrf-token" content="{token}"></head></html>'


class _Response:
    def __init__(self, status: int, text: str = "", body: object = None) -> None:
        self.status_code = status
        self.text = text
        self._body = body

    def json(self) -> object:
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _Session:
    """Answers GETs with a page carrying token N, and POSTs from a script."""

    def __init__(self, posts: list[_Response]) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self._posts = posts
        self._pages = 0

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        headers = kwargs.get("headers") or {}
        self.calls.append((method, url, dict(headers)))  # type: ignore[arg-type]
        if method == "GET":
            self._pages += 1
            return _Response(200, PAGE.format(token=f"t{self._pages}"))
        return self._posts.pop(0)

    def close(self) -> None:
        pass


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _http(posts: list[_Response], clock: _Clock | None = None) -> tuple[ChartinkHttp, _Session]:
    session = _Session(posts)
    clock = clock or _Clock()
    http = ChartinkHttp(clock=clock, sleep=clock.sleep, session=session)  # type: ignore[arg-type]
    return http, session


def test_first_post_fetches_a_token_and_sends_it():
    http, session = _http([_Response(200, body={"data": []})])
    assert http.post("/screener/process", {"scan_clause": "x"}) == {"data": []}
    (get, _, _), (post, url, headers) = session.calls
    assert (get, post, url) == ("GET", "POST", "https://chartink.com/screener/process")
    assert headers["x-csrf-token"] == "t1"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


def test_a_stale_token_is_refreshed_once_and_the_post_retried():
    http, session = _http(
        [
            _Response(200, body={"data": []}),
            _Response(419, body={"message": "CSRF token mismatch."}),
            _Response(200, body={"data": [1]}),
        ]
    )
    http.post("/screener/process", {"scan_clause": "x"})
    assert http.post("/screener/process", {"scan_clause": "x"}) == {"data": [1]}
    tokens = [h.get("x-csrf-token") for m, _, h in session.calls if m == "POST"]
    assert tokens == ["t1", "t1", "t2"]


def test_a_second_mismatch_is_an_error_not_a_loop():
    http, _ = _http([_Response(419), _Response(419)])
    with pytest.raises(ChartinkUnavailableError, match="419"):
        http.post("/screener/process", {"scan_clause": "x"})


@pytest.mark.parametrize("status", [403, 429, 503])
def test_blocking_statuses_say_to_back_off(status):
    http, _ = _http([_Response(status)])
    with pytest.raises(ChartinkUnavailableError, match="rate"):
        http.post("/widget/process", {"query": "select 1"})


def test_requests_are_spaced_at_least_a_second_apart():
    clock = _Clock()
    http, session = _http([_Response(200, body={}), _Response(200, body={})], clock)
    http.post("/screener/process", {"scan_clause": "x"})  # GET token, then POST
    http.post("/screener/process", {"scan_clause": "y"})
    assert len(session.calls) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_network_errors_become_unavailable():
    http, session = _http([])

    def boom(*args: object, **kwargs: object) -> _Response:
        raise requests.ConnectionError("offline")

    session.request = boom  # type: ignore[method-assign]
    with pytest.raises(ChartinkUnavailableError, match="reach Chartink"):
        http.page("https://chartink.com/dashboard/1")
