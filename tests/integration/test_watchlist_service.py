import pytest

from swingdash.adapters.storage.db import Database
from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.adapters.storage.repos.watchlists import WatchlistRepository
from swingdash.bootstrap import default_watchlist_symbols
from swingdash.domain.watchlist import DEFAULT_WATCHLIST_NAME, Watchlist
from swingdash.services.watchlists import WatchlistExistsError, WatchlistService


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


def test_renaming_while_editing_replaces_the_list_instead_of_copying_it(db: Database):
    """Editing 'nxtDay' and saving as '00nxtDay' used to leave both behind."""
    service = _service(db)
    service.save("nxtDay", ["RAYMOND"])
    service.set_active("nxtDay")

    service.save("00nxtDay", ["RAYMOND", "SBIN"], replacing="nxtDay")

    assert [w.name for w in service.all()] == ["00nxtDay"]
    assert service.get("00nxtDay") == Watchlist("00nxtDay", ("RAYMOND", "SBIN"))
    assert service.active_name() == "00nxtDay"


def test_renaming_persists_across_a_restart(db: Database, tmp_path):
    service = _service(db)
    service.save("nxtDay", ["RAYMOND"])
    service.save("00nxtDay", ["RAYMOND"], replacing="nxtDay")
    db.close()

    reopened = Database(db.path)
    try:
        assert [w.name for w in _service(reopened).all()] == ["00nxtDay"]
    finally:
        reopened.close()


def test_editing_without_renaming_updates_in_place(db: Database):
    service = _service(db)
    service.save("W01", ["SBIN"])
    service.save("W01", ["SBIN", "TCS"], replacing="W01")
    assert service.all() == [Watchlist("W01", ("SBIN", "TCS"))]


def test_a_name_that_belongs_to_another_list_is_refused(db: Database):
    service = _service(db)
    service.save("W01", ["SBIN"])
    service.save("nxtDay", ["RAYMOND"])

    with pytest.raises(WatchlistExistsError):
        service.save("W01", ["TCS"])  # new list reusing a name
    with pytest.raises(WatchlistExistsError):
        service.save("W01", ["RAYMOND"], replacing="nxtDay")  # rename onto it

    assert service.get("W01") == Watchlist("W01", ("SBIN",))
    assert service.get("nxtDay") == Watchlist("nxtDay", ("RAYMOND",))


def test_deleting_persists_across_a_restart(db: Database):
    service = _service(db)
    service.save("nxtDay", ["RAYMOND"])
    service.delete("nxtDay")
    db.close()

    reopened = Database(db.path)
    try:
        assert _service(reopened).all() == []
    finally:
        reopened.close()
