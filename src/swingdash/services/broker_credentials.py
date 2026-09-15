"""
Where a broker's client id and access token are kept: the user-dir `.env`,
like the Upstox token - never the repo, never the database, never a log
(every token is registered for redaction as soon as it's seen).
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from dotenv import set_key, unset_key

from swingdash.domain.broker import BrokerCredentials
from swingdash.logging_setup import redact


class BrokerCredentialsStore:
    def __init__(
        self,
        env_file: Path,
        client_id_key: str,
        token_key: str,
        initial: BrokerCredentials | None = None,
        *,
        persist: bool = True,  # False in tests: keep it in memory only
    ) -> None:
        self._env_file = env_file
        self._client_id_key = client_id_key
        self._token_key = token_key
        self._persist = persist
        self._lock = threading.Lock()
        self._current = initial if initial and initial.access_token else None
        if self._current:
            redact(self._current.access_token)

    def current(self) -> BrokerCredentials | None:
        with self._lock:
            return self._current

    def save(self, client_id: str, access_token: str) -> None:
        client_id, access_token = client_id.strip(), access_token.strip()
        redact(access_token)
        with self._lock:
            self._current = BrokerCredentials(client_id, access_token)
            if not self._persist:
                return
            self._env_file.parent.mkdir(parents=True, exist_ok=True)
            self._env_file.touch(exist_ok=True)
            set_key(str(self._env_file), self._client_id_key, client_id)
            set_key(str(self._env_file), self._token_key, access_token)
            with contextlib.suppress(OSError):
                os.chmod(self._env_file, 0o600)

    def clear(self) -> None:
        with self._lock:
            self._current = None
            if self._persist and self._env_file.is_file():
                unset_key(str(self._env_file), self._token_key)
