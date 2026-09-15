"""
Parsers for Dhan's JSON: holdings, today's positions, trade book, trade
history, profile and token renewal. Pure - JSON in, domain objects out.

Shapes from Dhan's v2 docs (docs/dhan-api-docs.md), including the trade
history sample, which shows two things worth knowing: `tradingSymbol` can be
null there (the stock is identified by `isin`, with a display name in
`customSymbol`), and times are IST without an offset ("2022-12-30 10:00:46").
"NA" stands for no value. Numbers may arrive as strings.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any

from swingdash.adapters.dhan.http import DhanFormatError
from swingdash.domain.broker import (
    BrokerAccount,
    BrokerDayPosition,
    BrokerHolding,
    BrokerToken,
    BrokerTrade,
    Side,
)
from swingdash.domain.calendar import IST

# The charges Dhan itemises on each historical fill.
_CHARGE_FIELDS = (
    "brokerageCharges",
    "stt",
    "stampDuty",
    "exchangeTransactionCharges",
    "sebiTax",
    "serviceTax",
)
_EQUITY_SEGMENTS = {"NSE_EQ", "BSE_EQ"}


def parse_holdings(payload: Any) -> list[BrokerHolding]:
    holdings: list[BrokerHolding] = []
    for record in _records(payload, "holdings"):
        if str(record.get("exchange") or "NSE").upper() not in {"NSE", "ALL", "BSE"}:
            continue
        mtf = _int(record.get("mtf_qty")) + _int(record.get("mtf_t1_qty"))
        holdings.append(
            BrokerHolding(
                symbol=_text(record.get("tradingSymbol")),
                isin=_optional_text(record.get("isin")),
                quantity=_int(record.get("totalQty")),
                avg_cost=_float(record.get("avgCostPrice")),
                mtf_quantity=mtf,
                security_id=_optional_text(record.get("securityId")),
            )
        )
    return holdings


def parse_positions(payload: Any) -> list[BrokerDayPosition]:
    return [
        BrokerDayPosition(
            symbol=_text(record.get("tradingSymbol")),
            security_id=_optional_text(record.get("securityId")),
            product=_text(record.get("productType")).upper(),
            net_quantity=_int(record.get("netQty")),
            day_buy_quantity=_int(record.get("dayBuyQty")),
            day_sell_quantity=_int(record.get("daySellQty")),
        )
        for record in _records(payload, "positions")
        if str(record.get("exchangeSegment") or "NSE_EQ").upper() in _EQUITY_SEGMENTS
    ]


def parse_trades(payload: Any) -> list[BrokerTrade]:
    """The trade book (today) and trade history pages share this shape."""
    trades: list[BrokerTrade] = []
    for record in _records(payload, "trades"):
        if str(record.get("exchangeSegment") or "").upper() not in _EQUITY_SEGMENTS:
            continue
        side = str(record.get("transactionType") or "").upper()
        quantity = _int(record.get("tradedQuantity"))
        time = _time(record.get("exchangeTime")) or _time(record.get("updateTime"))
        if side not in {"BUY", "SELL"} or quantity <= 0 or time is None:
            continue
        price = _float(record.get("tradedPrice"))
        trades.append(
            BrokerTrade(
                trade_id=fill_id(_text(record.get("orderId")), time, quantity, price),
                symbol=_text(record.get("tradingSymbol")),
                isin=_optional_text(record.get("isin")),
                side=Side(side),
                product=_text(record.get("productType")).upper(),
                quantity=quantity,
                price=price,
                time=time,
                charges=sum(_float(record.get(name)) for name in _CHARGE_FIELDS),
                security_id=_optional_text(record.get("securityId")),
            )
        )
    return trades


def fill_id(order_id: str, time: dt.datetime, quantity: int, price: float) -> str:
    """
    A fill's identity: its order, exchange time, quantity and price. Not
    `exchangeTradeId` - trade history sends "0" for every fill (verified on a
    real account, Sep 2026), which would merge an order's partial fills, and
    the trade book may send the real one, which wouldn't match history's.
    """
    return f"{order_id}|{time:%Y-%m-%dT%H:%M:%S}|{quantity}|{price:.4f}"


def parse_account(payload: Any, token: str) -> BrokerAccount:
    if not isinstance(payload, dict) or not payload.get("dhanClientId"):
        raise DhanFormatError("unexpected profile response shape")
    return BrokerAccount(
        client_id=str(payload["dhanClientId"]),
        name=str(payload.get("dhanClientName") or ""),
        token_valid_until=_time(payload.get("tokenValidity")) or token_expiry(token),
    )


def parse_renewed_token(payload: Any) -> BrokerToken:
    if not isinstance(payload, dict) or not payload.get("accessToken"):
        raise DhanFormatError("unexpected token renewal response shape")
    token = str(payload["accessToken"])
    return BrokerToken(token, _time(payload.get("expiryTime")) or token_expiry(token))


def token_expiry(token: str) -> dt.datetime | None:
    """A Dhan access token is a JWT; its `exp` claim says when it lapses."""
    try:
        claims_part = token.split(".")[1]
        padded = claims_part + "=" * (-len(claims_part) % 4)
        claims: Any = json.loads(base64.urlsafe_b64decode(padded))
        return dt.datetime.fromtimestamp(float(claims["exp"]), IST)
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def _records(payload: Any, what: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        payload = payload["data"]
    if not isinstance(payload, list):
        raise DhanFormatError(f"unexpected {what} response shape")
    return [record for record in payload if isinstance(record, dict)]


def _time(value: Any) -> dt.datetime | None:
    if value is None or str(value).strip().upper() in {"", "NA", "NULL"}:
        return None
    text = str(value).strip()
    if text.isdigit():  # epoch seconds or milliseconds
        number = int(text)
        return dt.datetime.fromtimestamp(number / 1000 if number > 10**11 else number, IST)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(text[:19], fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def _text(value: Any) -> str:
    return "" if value is None or str(value).strip().upper() == "NA" else str(value).strip()


def _optional_text(value: Any) -> str | None:
    return _text(value) or None


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    return int(_float(value))
