"""Errors shared across layers, so services can react without importing adapters."""

from __future__ import annotations


class RateLimitedError(RuntimeError):
    """The data provider refused the call for exceeding its rate limit; retry later."""


class BrokerAuthError(RuntimeError):
    """The broker rejected the credentials (e.g. an expired access token)."""


class BrokerUnavailableError(RuntimeError):
    """The broker couldn't be reached or refused the request; the message says why."""
