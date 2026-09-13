import threading

from swingdash.adapters.upstox.client import LazyThreadPool, UpstoxClient


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
