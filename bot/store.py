"""KV store backed by SQLite, with lazy TTL expiry.

Every storage call in the bot reduces to six operations against a keyed
value: get / set / set_nx / delete / incr / expire — all TTL-aware.
SqliteStore implements them against a local SQLite file, which is the
right fit for hosts with persistent disk (PythonAnywhere).

Lazy expiry: expired rows are filtered on read and overwritten on
write. There is no background sweeper — stale rows accumulate slowly
but never affect correctness. PA free disk is 512MB; the bot's
entire state stays well under 10MB even with hundreds of users.

WAL mode is enabled so the (currently unused) future case of
multiple workers won't have readers blocking writers.

Concurrency: the sqlite3 connection is opened with `check_same_thread=False`
because keep_typing()'s daemon thread shares the worker. A `threading.Lock`
serializes Python-level access so the (not thread-safe) Connection object
isn't used concurrently. Multi-statement operations (incr, set_nx) use
`BEGIN IMMEDIATE` so they're also atomic across processes if PA ever
gives us multiple workers.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Optional

import requests


class SqliteStore:
    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            expires_at INTEGER
        )
    """

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(self._SCHEMA)
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM kv WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at is not None and expires_at <= self._now():
            return None
        return value

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        expires_at = self._now() + ex if ex else None
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value, expires_at),
            )

    def set_nx(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set key=value only if the key is absent or expired.

        Returns True if we set the value (i.e. caller "won" the claim),
        False if a non-expired entry already exists. Atomic — used by
        dedupe to prevent two concurrent webhook requests from both
        processing the same update_id.
        """
        expires_at = self._now() + ex if ex else None
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT expires_at FROM kv WHERE key = ?", (key,)
                ).fetchone()
                now = self._now()
                if row is not None and (row[0] is None or row[0] > now):
                    self._conn.execute("COMMIT")
                    return False
                self._conn.execute(
                    "INSERT OR REPLACE INTO kv (key, value, expires_at) "
                    "VALUES (?, ?, ?)",
                    (key, value, expires_at),
                )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def incr(self, key: str) -> int:
        """Atomic increment with Redis INCR semantics.

        Missing or expired keys are (re)created at 1 with no TTL.
        Existing keys keep their TTL untouched. Wrapped in BEGIN
        IMMEDIATE so concurrent callers don't lose updates — codex
        review caught the previous read-then-write race.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT value, expires_at FROM kv WHERE key = ?", (key,)
                ).fetchone()
                now = self._now()
                if row is None or (row[1] is not None and row[1] <= now):
                    self._conn.execute(
                        "INSERT OR REPLACE INTO kv (key, value, expires_at) "
                        "VALUES (?, '1', NULL)",
                        (key,),
                    )
                    new_value = 1
                else:
                    new_value = int(row[0]) + 1
                    self._conn.execute(
                        "UPDATE kv SET value = ? WHERE key = ?",
                        (str(new_value), key),
                    )
                self._conn.execute("COMMIT")
                return new_value
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def expire(self, key: str, ex: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE kv SET expires_at = ? WHERE key = ?",
                (self._now() + ex, key),
            )


class RedisStore:
    """The same six-op KV-with-TTL interface as SqliteStore, backed by the
    Upstash Redis REST API. A drop-in backend selected in bot/clients.py.

    Why REST (not a redis:// TCP client): serverless functions (Vercel) are
    short-lived and can't hold a pooled TCP connection, and SQLite can't
    persist on their read-only / ephemeral disk. Upstash's HTTP REST API is
    stateless per request, so it fits serverless — and each op below is a
    single Redis command whose semantics match the SQLite implementation
    exactly (INCR creates-at-1 and keeps TTL; SET NX = claim-if-absent).

    The REST protocol: POST the base URL a JSON array `["SET","k","v","EX",
    "60"]` with a Bearer token; the reply is `{"result": ...}` on success or
    `{"error": ...}` on failure. Errors raise — bot/history.py, rate_limit.py,
    preferences.py and dedupe.py already wrap every store call in try/except
    and fall back to safe defaults, so a transient Upstash blip degrades
    gracefully instead of crashing the handler.
    """

    def __init__(self, url: str, token: str, timeout: int = 10) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout
        # Validate URL + token once at init so a misconfiguration falls back to
        # stateless mode (via clients._init_redis) with a single clear log
        # line, instead of erroring on every request.
        if self._cmd("PING") != "PONG":
            raise RuntimeError("Upstash PING did not return PONG")

    def _cmd(self, *args):
        resp = requests.post(
            self._url,
            headers=self._headers,
            json=[str(a) for a in args],
            timeout=self._timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"Upstash error: {payload['error']}")
        return payload.get("result")

    def get(self, key: str) -> Optional[str]:
        return self._cmd("GET", key)

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        if ex:
            self._cmd("SET", key, value, "EX", ex)
        else:
            self._cmd("SET", key, value)

    def set_nx(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """SET key value NX [EX ex] — set only if absent. Redis auto-drops
        expired keys, so an expired entry counts as absent (matching
        SqliteStore.set_nx). Returns True iff we won the claim."""
        if ex:
            result = self._cmd("SET", key, value, "EX", ex, "NX")
        else:
            result = self._cmd("SET", key, value, "NX")
        return result == "OK"

    def delete(self, key: str) -> None:
        self._cmd("DEL", key)

    def incr(self, key: str) -> int:
        return int(self._cmd("INCR", key))

    def expire(self, key: str, ex: int) -> None:
        self._cmd("EXPIRE", key, ex)
