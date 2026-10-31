"""Personal-key store for the Zeno transcription service.

Postgres when DATABASE_URL is set (Railway), otherwise a local SQLite file so the service runs and tests on a laptop.
Only a SHA-256 hash of each key is stored, so keys can't be read back, not even by the owner.
Keys are 256-bit random values, so a plain fast hash is enough (there is nothing to brute-force).
"""
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_ACTIVE_KEYS_PER_EMAIL = int(os.getenv("ZENO_MAX_KEYS_PER_EMAIL", "3"))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    def __init__(self, database_url: Optional[str] = None, sqlite_path: Optional[str] = None):
        self.url = database_url or ""
        self.postgres = self.url.startswith(("postgres://", "postgresql://"))
        self.lock = threading.Lock()
        if self.postgres:
            import psycopg  # imported only when needed, so SQLite-only use has no dependency
            self._psycopg = psycopg
            self.backend = "postgres"
        else:
            path = sqlite_path or os.getenv("ZENO_DB_PATH") or str(Path(__file__).resolve().parent / "zeno_keys.db")
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.backend = f"sqlite:{path}"
        self._init_schema()

    # -- plumbing ----------------------------------------------------------------------------
    def _run(self, sql: str, params: tuple = (), fetch: str = "none") -> Any:
        """Run one statement. SQL is written with %s placeholders for both backends."""
        with self.lock:
            if self.postgres:
                with self._psycopg.connect(self.url, autocommit=True, connect_timeout=10) as conn:
                    cur = conn.execute(sql, params)
                    return cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else None
            cur = self._sqlite.execute(sql.replace("%s", "?"), params)
            return cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else None

    def _init_schema(self) -> None:
        pk = "BIGSERIAL PRIMARY KEY" if self.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        real = "DOUBLE PRECISION" if self.postgres else "REAL"
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_keys (
            id {pk},
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            created_at {real} NOT NULL,
            last_used_at {real},
            revoked_at {real},
            requests INTEGER NOT NULL DEFAULT 0,
            audio_seconds {real} NOT NULL DEFAULT 0)""")

    # -- operations --------------------------------------------------------------------------
    def create(self, name: str, email: str) -> str:
        """Make a key for this person and return it. It is shown once and never stored in readable form."""
        name, email = name.strip(), email.strip().lower()
        active = self._run("SELECT COUNT(*) FROM zeno_keys WHERE email = %s AND revoked_at IS NULL", (email,), "one")[0]
        if active >= MAX_ACTIVE_KEYS_PER_EMAIL:
            raise ValueError(f"This email already has {active} active keys. Ask the owner to revoke one first.")
        key = "zeno_" + secrets.token_urlsafe(32)
        self._run("INSERT INTO zeno_keys (name, email, key_hash, created_at) VALUES (%s, %s, %s, %s)", (name, email, hash_key(key), time.time()))
        return key

    def lookup(self, key: str) -> Optional[Tuple[int, str]]:
        """(id, name) for an active key, else None."""
        row = self._run("SELECT id, name FROM zeno_keys WHERE key_hash = %s AND revoked_at IS NULL", (hash_key(key),), "one")
        return (int(row[0]), row[1]) if row else None

    def record_use(self, key_id: int, audio_seconds: float) -> None:
        self._run("UPDATE zeno_keys SET last_used_at = %s, requests = requests + 1, audio_seconds = audio_seconds + %s WHERE id = %s",
                  (time.time(), float(audio_seconds), key_id))

    def list(self) -> List[Dict[str, Any]]:
        rows = self._run("SELECT id, name, email, created_at, last_used_at, revoked_at, requests, audio_seconds FROM zeno_keys ORDER BY id DESC", (), "all")
        cols = ("id", "name", "email", "created_at", "last_used_at", "revoked_at", "requests", "audio_seconds")
        return [dict(zip(cols, r)) for r in rows]

    def revoke(self, key_id: int) -> bool:
        before = self._run("SELECT COUNT(*) FROM zeno_keys WHERE id = %s AND revoked_at IS NULL", (key_id,), "one")[0]
        if before:
            self._run("UPDATE zeno_keys SET revoked_at = %s WHERE id = %s", (time.time(), key_id))
        return bool(before)
