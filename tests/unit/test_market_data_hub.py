import pytest

from swingdash.services.market_data_hub import MarketDataHub, TooManyInstrumentsError
from tests.fakes.feed import FakeFeedFactory


class Recorder:
    def __init__(self) -> None:
        self.ticks: list[tuple[str, int | None]] = []
        self.statuses: list[str] = []
        self.connections: list[str] = []

    def on_tick(self, key, vtt, ltp, prev_close) -> None:
        self.ticks.append((key, vtt))

    def on_status(self, status) -> None:
        self.statuses.append(status)

    def on_connection(self, state) -> None:
        self.connections.append(state)


@pytest.fixture
def factory() -> FakeFeedFactory:
    return FakeFeedFactory()


@pytest.fixture
def hub(factory: FakeFeedFactory) -> MarketDataHub:
    return MarketDataHub(factory)


def test_one_connection_is_shared_by_all_consumers(hub, factory):
    a, b = Recorder(), Recorder()
    hub.subscribe(["K1", "K2"], a.on_tick)
    hub.subscribe(["K2", "K3"], b.on_tick)

    assert len(factory.created) == 1
    assert factory.transport.started_with == ["K1", "K2"]
    assert factory.transport.subscribed == [["K3"]]  # K2 was already live


def test_ticks_reach_only_consumers_of_that_instrument(hub, factory):
    a, b = Recorder(), Recorder()
    hub.subscribe(["K1", "K2"], a.on_tick)
    hub.subscribe(["K2"], b.on_tick)

    factory.transport.tick("K1", vtt=10)
    factory.transport.tick("K2", vtt=20)
    factory.transport.tick("UNKNOWN", vtt=30)

    assert a.ticks == [("K1", 10), ("K2", 20)]
    assert b.ticks == [("K2", 20)]


def test_shared_instruments_stay_subscribed_until_the_last_consumer_leaves(hub, factory):
    a, b = Recorder(), Recorder()
    sub_a = hub.subscribe(["K1", "K2"], a.on_tick)
    sub_b = hub.subscribe(["K2"], b.on_tick)

    sub_a.close()
    assert factory.transport.unsubscribed == [["K1"]]
    sub_b.close()
    assert factory.transport.unsubscribed == [["K1"], ["K2"]]
    assert hub.instrument_count == 0
    assert not factory.transport.stopped  # the socket outlives its consumers


def test_update_sends_only_the_difference(hub, factory):
    sub = hub.subscribe(["K1", "K2"], Recorder().on_tick)
    sub.update(["K2", "K3"])
    assert factory.transport.unsubscribed == [["K1"]]
    assert factory.transport.subscribed == [["K3"]]


def test_reconnect_resends_the_full_desired_set(hub, factory):
    hub.subscribe(["K2", "K1"], Recorder().on_tick)
    factory.transport.connect()
    assert factory.transport.subscribed[-1] == ["K1", "K2"]
    assert hub.connection_state == "connected"


def test_a_failing_consumer_does_not_starve_the_others(hub, factory):
    healthy = Recorder()

    def broken(*_args) -> None:
        raise RuntimeError("bug in some tab")

    hub.subscribe(["K1"], broken)
    hub.subscribe(["K1"], healthy.on_tick)
    factory.transport.tick("K1", vtt=5)
    factory.transport.tick("K1", vtt=6)
    assert healthy.ticks == [("K1", 5), ("K1", 6)]


def test_status_and_connection_events_fan_out(hub, factory):
    rec = Recorder()
    hub.subscribe(["K1"], rec.on_tick, rec.on_status, rec.on_connection)
    factory.transport.status("NORMAL_OPEN")
    factory.transport.connect()
    assert rec.statuses == ["NORMAL_OPEN"]
    assert rec.connections == ["connected"]
    assert hub.market_status == "NORMAL_OPEN"


def test_instrument_limit_is_enforced(hub, monkeypatch):
    monkeypatch.setattr("swingdash.services.market_data_hub.MAX_INSTRUMENTS", 3)
    sub = hub.subscribe(["K1", "K2"], Recorder().on_tick)
    with pytest.raises(TooManyInstrumentsError):
        hub.subscribe(["K3", "K4"], Recorder().on_tick)
    sub.update(["K1", "K2", "K3"])  # exactly at the limit is fine
    assert hub.instrument_count == 3


def test_close_stops_the_feed(hub, factory):
    hub.subscribe(["K1"], Recorder().on_tick)
    hub.close()
    assert factory.transport.stopped
