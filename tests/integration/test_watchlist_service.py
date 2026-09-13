from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.watchlists import WatchlistRepository
from swingdash.bootstrap import default_watchlist_symbols
from swingdash.domain.watchlist import DEFAULT_WATCHLIST_NAME, Watchlist
from swingdash.services.watchlists import WatchlistService


def _service(db: Database) -> WatchlistService:
    return WatchlistService(
        WatchlistRepository(db), AppStateRepository(db), lambda: ["TCS", "INFY"]
    )


def test_first_run_seeds_the_default_list_once(db: Database):
    service = _service(db)
    service.seed_on_first_run()
    assert service.get(DEFAULT_WATCHLIST_NAME) == Watchlist(DEFAULT_WATCHLIST_NAME, ("TCS", "INFY"))


def test_deleted_default_list_is_not_resurrected(db: Database):
    """Seeding used to run on every listing, so deleting 'default' never stuck."""
    service = _service(db)
    service.seed_on_first_run()
    assert service.delete(DEFAULT_WATCHLIST_NAME)
    service.seed_on_first_run()
    assert service.all() == []


def test_existing_lists_are_not_seeded_over(db: Database):
    service = _service(db)
    service.save("nxtDay", ["RAYMOND"])
    service.seed_on_first_run()
    assert [w.name for w in service.all()] == ["nxtDay"]


def test_initial_prefers_request_then_last_active_then_default(db: Database):
    service = _service(db)
    service.save(DEFAULT_WATCHLIST_NAME, ["TCS"])
    service.save("W01", ["SBIN"])
    service.save("nxtDay", ["RAYMOND"])

    assert service.initial("W01").name == "W01"  # type: ignore[union-attr]
    assert service.initial("missing") is None
    assert service.initial().name == DEFAULT_WATCHLIST_NAME  # type: ignore[union-attr]
    service.set_active("nxtDay")
    assert service.initial().name == "nxtDay"  # type: ignore[union-attr]


def test_deleting_the_active_list_clears_it(db: Database):
    service = _service(db)
    service.save("W01", ["SBIN"])
    service.set_active("W01")
    service.delete("W01")
    assert service.active_name() is None


def test_packaged_seed_is_readable():
    assert default_watchlist_symbols()
