"""Dhan adapter: parsers against synthetic responses shaped like Dhan's v2 docs, and HTTP handling."""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from swingdash.adapters.dhan import parsers
from swingdash.adapters.dhan.http import DhanCredentials, DhanFormatError, DhanHttp
from swingdash.adapters.dhan.source import DhanSource
from swingdash.domain.broker import Side
from swingdash.domain.calendar import IST
from swingdash.domain.errors import BrokerAuthError, BrokerUnavailableError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "dhan"
CREDENTIALS = DhanCredentials("1000000000", "header.payload.signature")


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _jwt(exp: int) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzUxMiJ9.{claims}.signature"


# --- parsers ----------------------------------------------------------------------


def test_holdings_split_delivery_and_mtf_and_accept_string_numbers():
    graphite, hfcl = parsers.parse_holdings(_json("holdings.json"))
    assert (graphite.symbol, graphite.isin, graphite.quantity, graphite.avg_cost) == (
        "GRAPHITE",
        "INE371A01025",
        5,
        855.85,
    )
    assert (hfcl.quantity, hfcl.avg_cost, hfcl.mtf_quantity, hfcl.security_id) == (
        40,
        101.2,
        20,
        "21951",
    )


def test_positions_keep_only_equity_segments():
    (position,) = parsers.parse_positions(_json("positions.json"))
    assert (position.symbol, position.product, position.day_buy_quantity) == (
        "TATAMOTORS",
        "CNC",
        10,
    )


def test_trade_book_fill():
    (trade,) = parsers.parse_trades(_json("trade_book.json"))
    assert trade.trade_id == "112111182045-15112111182938"
    assert (trade.symbol, trade.side, trade.product, trade.quantity, trade.price) == (
        "TATAMOTORS",
        Side.BUY,
        "CNC",
        10,
        700.5,
    )
    assert trade.time == dt.datetime(2026, 9, 15, 10, 12, 31, tzinfo=IST)
    assert trade.isin is None and trade.charges == 0


def test_trade_history_identifies_by_isin_and_sums_charges():
    (trade,) = parsers.parse_trades(_json("trade_history_page0.json"))  # F&O row skipped
    assert trade.symbol == ""  # tradingSymbol is null in history - resolved by ISIN later
    assert (trade.isin, trade.security_id) == ("INE371A01025", "1234")
    assert trade.charges == pytest.approx(0.0004 + 4.28 + 0.14 + 0.13 + 0.64)
    assert trade.time == dt.datetime(2026, 9, 9, 11, 2, 46, tzinfo=IST)


def test_profile_and_token_expiry():
    account = parsers.parse_account(_json("profile.json"), CREDENTIALS.access_token)
    assert (account.client_id, account.name) == ("1000000000", "TEST USER")
    assert account.token_valid_until == dt.datetime(2026, 9, 16, 9, 30, tzinfo=IST)

    expiry = parsers.token_expiry(_jwt(1789530000))
    assert expiry == dt.datetime.fromtimestamp(1789530000, IST)
    assert parsers.token_expiry("not-a-jwt") is None


def test_renewed_token_falls_back_to_its_jwt_expiry():
    token = _jwt(1789616400)
    renewed = parsers.parse_renewed_token({"accessToken": token, "expiryTime": "NA"})
    assert renewed.token == token
    assert renewed.valid_until == dt.datetime.fromtimestamp(1789616400, IST)
    assert token not in repr(renewed)  # never logged by accident


@pytest.mark.parametrize("payload", [None, {"data": "x"}, "text"])
def test_unexpected_shapes_fail_loudly(payload):
    with pytest.raises(DhanFormatError):
        parsers.parse_trades(payload)


# --- http ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str], timeout: object) -> _Response:
        self.calls.append((url, headers))
        return self.responses.pop(0)


def _http(
    *responses: _Response, credentials: DhanCredentials | None = CREDENTIALS
) -> tuple[DhanHttp, _Session]:
    session = _Session(*responses)
    http = DhanHttp(
        lambda: credentials,
        min_interval=0,
        session=session,  # type: ignore[arg-type]
    )
    return http, session


def test_requests_carry_the_token_headers():
    http, session = _http(_Response(200, []))
    assert http.get("holdings") == []
    url, headers = session.calls[0]
    assert url == "https://api.dhan.co/v2/holdings"
    assert headers["access-token"] == CREDENTIALS.access_token
    assert headers["client-id"] == "1000000000"


@pytest.mark.parametrize(
    "response",
    [
        _Response(401, None),
        _Response(
            400,
            {
                "errorType": "Invalid_Authentication",
                "errorCode": "DH-901",
                "errorMessage": "expired",
            },
        ),
    ],
)
def test_rejected_tokens_raise_an_auth_error(response):
    http, _ = _http(response)
    with pytest.raises(BrokerAuthError, match=r"web.dhan.co"):
        http.get("holdings")


def test_rate_limits_and_network_errors_are_unavailable():
    http, _ = _http(_Response(429, None))
    with pytest.raises(BrokerUnavailableError, match="rate limiting"):
        http.get("holdings")

    class _Broken:
        def get(self, *args: object, **kwargs: object) -> _Response:
            raise requests.ConnectionError("offline")

    broken = DhanHttp(lambda: CREDENTIALS, min_interval=0, session=_Broken())  # type: ignore[arg-type]
    with pytest.raises(BrokerUnavailableError, match="couldn't be reached"):
        broken.get("holdings")


def test_no_credentials_means_not_connected():
    http, session = _http(credentials=None)
    with pytest.raises(BrokerAuthError, match="isn't connected"):
        http.get("holdings")
    assert session.calls == []


def test_requests_are_spaced_out():
    now = [0.0]
    slept: list[float] = []
    session = _Session(_Response(200, []), _Response(200, []))
    http = DhanHttp(
        lambda: CREDENTIALS,
        min_interval=0.5,
        clock=lambda: now[0],
        sleep=lambda s: slept.append(s) or now.__setitem__(0, now[0] + s),
        session=session,  # type: ignore[arg-type]
    )
    http.get("a")
    http.get("b")
    assert slept == [0.5]


# --- source -------------------------------------------------------------------------


class _FakeHttp:
    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.paths: list[str] = []

    def get(self, path: str, headers: dict[str, str] | None = None) -> Any:
        self.paths.append(path)
        return self.pages.get(path, [])


def test_trade_history_reads_every_page_of_every_window():
    history = _json("trade_history_page0.json")
    http = _FakeHttp(
        {
            "trades/2026-01-01/2026-03-31/0": history,
            "trades/2026-01-01/2026-03-31/1": history,
            "trades/2026-04-01/2026-04-10/0": history,
        }
    )
    source = DhanSource(lambda: CREDENTIALS, http=http)  # type: ignore[arg-type]
    trades = source.trade_history(dt.date(2026, 1, 1), dt.date(2026, 4, 10))
    assert len(trades) == 3
    assert http.paths == [
        "trades/2026-01-01/2026-03-31/0",
        "trades/2026-01-01/2026-03-31/1",
        "trades/2026-01-01/2026-03-31/2",
        "trades/2026-04-01/2026-04-10/0",
        "trades/2026-04-01/2026-04-10/1",
    ]


def test_renewal_sends_the_client_id():
    http = _FakeHttp({"RenewToken": {"accessToken": _jwt(1789616400), "expiryTime": "NA"}})
    calls: list[dict[str, str] | None] = []
    original = http.get

    def recording(path: str, headers: dict[str, str] | None = None) -> Any:
        calls.append(headers)
        return original(path, headers)

    http.get = recording  # type: ignore[method-assign]
    source = DhanSource(lambda: CREDENTIALS, http=http)  # type: ignore[arg-type]
    assert source.renew_token().valid_until is not None
    assert calls == [{"dhanClientId": "1000000000"}]
