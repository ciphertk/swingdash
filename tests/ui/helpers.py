from __future__ import annotations

import asyncio
from collections.abc import Callable


def key(symbol: str) -> str:
    return f"NSE_EQ|{symbol}"


async def until(pilot, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Let the app run until `predicate` holds - for work on timers or threads."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await pilot.pause()
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")
