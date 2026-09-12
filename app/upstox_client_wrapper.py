"""
Thin wrapper around the official `upstox_client` SDK (package name on PyPI:
upstox-python-sdk). Every other module should get its Upstox API instances
through here, rather than constructing upstox_client.Configuration() itself -
that way, if auth ever changes (e.g. you add a second, order-placing OAuth
token alongside the read-only Analytics Token), it changes in one place.
"""
import upstox_client

from app.config import require_token


def get_configuration() -> upstox_client.Configuration:
    configuration = upstox_client.Configuration()
    configuration.access_token = require_token()
    return configuration


def get_api_client() -> upstox_client.ApiClient:
    return upstox_client.ApiClient(get_configuration())


def user_api() -> upstox_client.UserApi:
    return upstox_client.UserApi(get_api_client())


def market_quote_v3_api() -> upstox_client.MarketQuoteV3Api:
    return upstox_client.MarketQuoteV3Api(get_api_client())


def history_v3_api() -> upstox_client.HistoryV3Api:
    return upstox_client.HistoryV3Api(get_api_client())


def fundamentals_api() -> upstox_client.FundamentalsApi:
    return upstox_client.FundamentalsApi(get_api_client())
