"""Errors shared across layers, so services can react without importing adapters."""

from __future__ import annotations


class RateLimitedError(RuntimeError):
    """The data provider refused the call for exceeding its rate limit; retry later."""
