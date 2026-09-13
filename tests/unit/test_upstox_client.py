import threading

from swingdash.adapters.upstox.client import LazyThreadPool, RateLimiter, UpstoxClient


class _Clock:
    """A clock that only moves when the limiter sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_lets_calls_through_until_a_window_fills():
    clock = _Clock()
    limiter = RateLimiter([(3, 1.0)], clock=clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.sleeps == []

    limiter.acquire()  # the 4th waits for the oldest call to leave the window
    assert clock.sleeps == [1.0]


def test_rate_limiter_respects_the_tightest_of_several_windows():
    clock = _Clock()
    limiter = RateLimiter([(10, 1.0), (4, 60.0)], clock=clock, sleep=clock.sleep)
    for _ in range(4):
        limiter.acquire()
    limiter.acquire()
    assert sum(clock.sleeps) == 60.0  # per-second room, but the minute is full


def test_pause_holds_every_caller_back():
    clock = _Clock()
    limiter = RateLimiter([(100, 1.0)], clock=clock, sleep=clock.sleep)
    limiter.pause(60)
    limiter.acquire()
    assert sum(clock.sleeps) == 60


def test_call_adds_a_timeout_and_turns_429_into_a_pause():
    from upstox_client.rest import ApiException

    from swingdash.adapters.upstox.client import RATE_LIMIT_PAUSE_SECONDS, REQUEST_TIMEOUT
    from swingdash.domain.errors import RateLimitedError

    clock = _Clock()
    limiter = RateLimiter([(100, 1.0)], clock=clock, sleep=clock.sleep)
    client = UpstoxClient(lambda: "token", limiter=limiter)
    seen: dict[str, object] = {}

    def ok(key: str, _request_timeout: object = None) -> str:
        seen["timeout"] = _request_timeout
        return key

    assert client.call(ok, "RELIANCE") == "RELIANCE"
    assert seen["timeout"] == REQUEST_TIMEOUT

    def throttled(_request_timeout: object = None) -> None:
        raise ApiException(status=429, reason="Too Many Requests")

    try:
        client.call(throttled)
    except RateLimitedError:
        pass
    else:
        raise AssertionError("expected RateLimitedError")
    limiter.acquire()
    assert sum(clock.sleeps) == RATE_LIMIT_PAUSE_SECONDS


def test_metered_accessors_take_a_slot_but_the_feed_client_does_not():
    taken: list[int] = []

    class Counting(RateLimiter):
        def acquire(self) -> None:
            taken.append(1)

    client = UpstoxClient(lambda: "token", limiter=Counting([]))
    client.history_v3()
    client.market_quote_v3()
    client.api_client()  # what the live feed uses
    assert len(taken) == 2


def test_building_sdk_clients_starts_no_threads():
    """Each ApiClient used to spawn a pool thread per CPU for calls we never make."""
    before = threading.active_count()
    clients = [UpstoxClient(lambda: "token").api_client() for _ in range(5)]

    assert threading.active_count() == before
    assert all(isinstance(c.pool, LazyThreadPool) and not c.pool.started for c in clients)


def test_teardown_of_an_unused_pool_is_a_no_op():
    """What the SDK's ApiClient.__del__ calls - must not touch any OS handles."""
    pool = LazyThreadPool()
    pool.close()
    pool.join()
    assert not pool.started


def test_the_sdks_async_path_still_works_on_demand():
    pool = LazyThreadPool()
    try:
        assert pool.apply_async(lambda a, b: a + b, (2, 3)).get(timeout=5) == 5
        assert pool.started
    finally:
        pool.close()
        pool.join()
