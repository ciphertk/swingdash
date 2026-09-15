"""
Runtime settings: where data lives and how to authenticate.

Resolved once at startup - never at import time - from, highest priority
first: process environment, `.env` in the user config directory, `.env` in
the current directory (a convenience while developing from the repo).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
from platformdirs import PlatformDirs

APP_NAME = "swingdash"
TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"
# Dhan (read-only portfolio sync). The token lasts 24h and is renewed and
# written back by swingdash while it's still valid.
DHAN_CLIENT_ID_ENV = "DHAN_CLIENT_ID"
DHAN_TOKEN_ENV = "DHAN_ACCESS_TOKEN"
HOME_ENV = "SWINGDASH_HOME"

DEFAULT_MSWING_INDEX_KEY = "NSE_INDEX|NIFTY MIDSML 400"

# Public, unauthenticated instrument master. Upstox refreshes it daily
# (~6 AM IST).
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class MissingTokenError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @property
    def env_file(self) -> Path:
        return self.config_dir / ".env"

    @property
    def database(self) -> Path:
        return self.data_dir / "swingdash.db"

    @property
    def equity_instruments(self) -> Path:
        return self.cache_dir / "nse_equity_instruments.json"

    @property
    def index_instruments(self) -> Path:
        return self.cache_dir / "nse_index_instruments.json"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "swingdash.log"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "Exports"

    @classmethod
    def resolve(cls, environ: Mapping[str, str]) -> Paths:
        home = environ.get(HOME_ENV)
        if home:
            # One self-contained root - for development and tests.
            root = Path(home).expanduser()
            return cls(root / "config", root / "data", root / "cache", root / "logs")
        dirs = PlatformDirs(APP_NAME, appauthor=False)
        return cls(
            config_dir=Path(dirs.user_config_dir),
            data_dir=Path(dirs.user_data_dir),
            cache_dir=Path(dirs.user_cache_dir),
            log_dir=Path(dirs.user_log_dir),
        )

    def ensure(self) -> None:
        for directory in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    paths: Paths
    upstox_token: str = field(default="", repr=False)
    mswing_index_key: str = DEFAULT_MSWING_INDEX_KEY
    dhan_client_id: str = ""
    dhan_access_token: str = field(default="", repr=False)

    def require_token(self) -> str:
        if not self.upstox_token:
            raise MissingTokenError(
                f"{TOKEN_ENV} is not set. Run `swingdash setup`, or put it in "
                f"{self.paths.env_file} (generate it at account.upstox.com -> "
                "Developer Apps -> your app -> Analytics)."
            )
        return self.upstox_token


def load_settings(environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> Settings:
    process_env = dict(os.environ if environ is None else environ)
    paths = Paths.resolve(process_env)

    values: dict[str, str] = {}
    for env_file in ((cwd or Path.cwd()) / ".env", paths.env_file):  # lowest priority first
        if env_file.is_file():
            values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(process_env)

    return Settings(
        paths=paths,
        upstox_token=values.get(TOKEN_ENV, "").strip(),
        mswing_index_key=values.get("DEFAULT_MSWING_INDEX_KEY", DEFAULT_MSWING_INDEX_KEY),
        dhan_client_id=values.get(DHAN_CLIENT_ID_ENV, "").strip(),
        dhan_access_token=values.get(DHAN_TOKEN_ENV, "").strip(),
    )
