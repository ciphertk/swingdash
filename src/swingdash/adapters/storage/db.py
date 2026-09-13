"""
SQLite database handle.

Personal single-user tool at this scale (CLAUDE.md's scalability notes) -
SQLite is enough, no need for a client/server DB or an ORM.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    """
    One connection PER THREAD, never one shared connection. A sqlite3
    connection is not safe to use from more than one thread at a time, and
    baselines are built on a thread pool: a shared connection intermittently
    corrupted in-flight statements ("bad parameter or other API misuse").
    WAL plus a busy timeout let SQLite's file locking queue the rare
    concurrent write instead of failing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._opened: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False only so close() can close every
            # thread's connection at shutdown; each connection is still only
            # ever used by the thread that opened it.
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            with self._lock:
                self._opened.append(conn)
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        with self._lock:
            for conn in self._opened:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            self._opened.clear()
        self._local = threading.local()
