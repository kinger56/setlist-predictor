"""A tiny SQLite cache for setlist.fm responses.

Why cache? Setlists from last month don't change, so asking setlist.fm for
them on every page load wastes time and eats into the API's request limits.
We store each response with the time we fetched it and reuse it until it is
older than `ttl_seconds` ("time to live").

We use Python's built-in sqlite3 instead of an ORM to keep it simple: one
table, two queries.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Callable

DEFAULT_TTL_SECONDS = 12 * 60 * 60  # 12 hours: new shows appear daily at most


class Cache:
    def __init__(
        self,
        path: str = ":memory:",
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        # The web server may call us from several threads at once.
        # check_same_thread=False allows that, and the lock keeps two threads
        # from writing at the same moment.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._clock = clock  # injectable so tests can "fast-forward time"
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS api_cache (
                       key        TEXT PRIMARY KEY,
                       body       TEXT NOT NULL,
                       fetched_at REAL NOT NULL
                   )"""
            )
            self._conn.commit()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body, fetched_at FROM api_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if self._clock() - fetched_at > self._ttl:
            return None  # too old: treat as a miss so we fetch fresh data
        return body

    def set(self, key: str, body: str) -> None:
        # The "?" placeholders let SQLite insert values safely. Never build SQL
        # with f-strings: that's how SQL injection bugs happen.
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO api_cache (key, body, fetched_at) "
                "VALUES (?, ?, ?)",
                (key, body, self._clock()),
            )
            self._conn.commit()
