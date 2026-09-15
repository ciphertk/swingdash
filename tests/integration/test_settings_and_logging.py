import logging
from pathlib import Path

import pytest

from swingdash.logging_setup import configure_logging, redact
from swingdash.settings import MissingTokenError, load_settings


def test_swingdash_home_roots_every_path(isolated_home: Path):
    paths = load_settings().paths
    for directory in (paths.config_dir, paths.data_dir, paths.cache_dir, paths.log_dir):
        assert isolated_home in directory.parents
    assert paths.database.name == "swingdash.db"


def test_token_precedence_env_over_config_file_over_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = load_settings().paths  # resolved from the isolated SWINGDASH_HOME
    paths.ensure()
    (tmp_path / ".env").write_text("UPSTOX_ANALYTICS_TOKEN=from-cwd\n")
    assert load_settings(cwd=tmp_path).upstox_token == "from-cwd"

    paths.env_file.write_text("UPSTOX_ANALYTICS_TOKEN=from-config\n")
    assert load_settings(cwd=tmp_path).upstox_token == "from-config"

    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "from-env")
    assert load_settings(cwd=tmp_path).upstox_token == "from-env"


def test_token_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "super-secret-token-value")
    assert "super-secret" not in repr(load_settings())


def test_missing_token_raises_actionable_error():
    with pytest.raises(MissingTokenError, match="swingdash setup"):
        load_settings().require_token()


def test_log_file_redacts_the_token():
    settings = load_settings()
    configure_logging(settings.paths, "super-secret-token-value")
    try:
        logging.getLogger("test").warning("auth header Bearer %s", "super-secret-token-value")
        # A secret learned later (a renewed broker token) is scrubbed too.
        redact("renewed-dhan-token")
        logging.getLogger("test").warning("renewed to %s", "renewed-dhan-token")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = settings.paths.log_file.read_text(encoding="utf-8")
        assert "super-secret" not in text and "renewed-dhan" not in text
        assert "Bearer ****" in text and "renewed to ****" in text
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)
