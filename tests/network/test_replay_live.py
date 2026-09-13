"""
Real-data verification against Upstox. Deselected by default; run with
`uv run pytest -m network` on a machine where `swingdash setup` has run.
"""

import os

import pytest

from swingdash.bootstrap import build_services
from swingdash.services.rvol.replay import replay_symbol
from swingdash.settings import load_settings

pytestmark = pytest.mark.network


@pytest.fixture
def real_home(monkeypatch: pytest.MonkeyPatch):
    """Undo the test isolation - these tests need the real configured install."""
    real = os.environ.get("SWINGDASH_REAL_HOME")
    if real:
        monkeypatch.setenv("SWINGDASH_HOME", real)
    else:
        monkeypatch.delenv("SWINGDASH_HOME", raising=False)


@pytest.mark.parametrize("symbol", ["RELIANCE", "TCS", "HDFCBANK"])
def test_rvol_identities_hold_on_the_last_real_session(real_home, symbol):
    settings = load_settings()
    if not settings.upstox_token:
        pytest.skip("no Upstox token configured")
    services = build_services(settings)
    try:
        if not services.instruments.available():
            pytest.skip("instrument list not downloaded - run `swingdash setup`")
        result = replay_symbol(
            symbol,
            instruments=services.instruments,
            history=services.history,
            calendar=services.calendar,
        )
    finally:
        services.close()
    assert result.error is None
    assert result.passed, result
