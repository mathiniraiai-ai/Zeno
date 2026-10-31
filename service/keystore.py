"""Personal-key and token-telemetry store for the Zeno transcription service.

Postgres when DATABASE_URL is set (Railway), otherwise a local SQLite file so the service runs and tests on a laptop.
Only a SHA-256 hash of each key is stored, so keys can't be read back, not even by the owner.
Also stores per-clip Gemini transcription token counts and full per-job Agent + Clip token telemetry in `zeno_runs`.
"""
import hashlib
import json
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
            audio_seconds {real} NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0)""")
        # Migrate existing zeno_keys table if created before token columns existed
        for col in ("input_tokens", "output_tokens", "total_tokens"):
            try:
                if self.postgres:
                    self._run(f"ALTER TABLE zeno_keys ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL DEFAULT 0")
                else:
                    self._run(f"ALTER TABLE zeno_keys ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_runs (
            id {pk},
            key_id INTEGER,
            editor_name TEXT NOT NULL,
            project TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'auto',
            agent_name TEXT NOT NULL DEFAULT 'unknown',
            agent_model TEXT NOT NULL DEFAULT 'unknown',
            clips_count INTEGER NOT NULL DEFAULT 0,
            video_seconds {real} NOT NULL DEFAULT 0,
            transcribe_in_tokens INTEGER NOT NULL DEFAULT 0,
            transcribe_out_tokens INTEGER NOT NULL DEFAULT 0,
            transcribe_total_tokens INTEGER NOT NULL DEFAULT 0,
            agent_in_tokens INTEGER NOT NULL DEFAULT 0,
            agent_cached_tokens INTEGER NOT NULL DEFAULT 0,
            agent_out_tokens INTEGER NOT NULL DEFAULT 0,
            agent_total_tokens INTEGER NOT NULL DEFAULT 0,
            grand_total_tokens INTEGER NOT NULL DEFAULT 0,
            clips_json TEXT NOT NULL DEFAULT '[]',
            created_at {real} NOT NULL)""")

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

    def record_use(self, key_id: int, audio_seconds: float, input_tokens: int = 0, output_tokens: int = 0, total_tokens: int = 0) -> None:
        tot = int(total_tokens or (int(input_tokens) + int(output_tokens)))
        self._run(
            "UPDATE zeno_keys SET last_used_at = %s, requests = requests + 1, audio_seconds = audio_seconds + %s, "
            "input_tokens = input_tokens + %s, output_tokens = output_tokens + %s, total_tokens = total_tokens + %s WHERE id = %s",
            (time.time(), float(audio_seconds), int(input_tokens), int(output_tokens), tot, key_id),
        )

    def record_run(self, key_id: Optional[int], editor_name: str, payload: Dict[str, Any]) -> int:
        clips = payload.get("clips") if isinstance(payload.get("clips"), list) else []
        t_in = int(payload.get("transcribe_in_tokens") or 0)
        t_out = int(payload.get("transcribe_out_tokens") or 0)
        t_tot = int(payload.get("transcribe_total_tokens") or (t_in + t_out))
        a_in = int(payload.get("agent_in_tokens") or 0)
        a_cache = int(payload.get("agent_cached_tokens") or 0)
        a_out = int(payload.get("agent_out_tokens") or 0)
        a_tot = int(payload.get("agent_total_tokens") or (a_in + a_cache + a_out))
        grand = int(payload.get("grand_total_tokens") or (t_tot + a_tot))
        now = time.time()
        self._run(
            """INSERT INTO zeno_runs (
                key_id, editor_name, project, language, agent_name, agent_model,
                clips_count, video_seconds, transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens,
                clips_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                key_id,
                str(editor_name or "unknown")[:60],
                str(payload.get("project") or "unnamed")[:120],
                str(payload.get("language") or "auto")[:20],
                str(payload.get("agent_name") or "unknown")[:60],
                str(payload.get("agent_model") or "unknown")[:80],
                int(payload.get("clips_count") or len(clips)),
                float(payload.get("video_seconds") or 0.0),
                t_in, t_out, t_tot,
                a_in, a_cache, a_out, a_tot, grand,
                json.dumps(clips[:60], ensure_ascii=False),
                now,
            ),
        )
        row = self._run("SELECT MAX(id) FROM zeno_runs", (), "one")
        return int(row[0] or 0)

    def list(self) -> List[Dict[str, Any]]:
        rows = self._run(
            "SELECT id, name, email, created_at, last_used_at, revoked_at, requests, audio_seconds, input_tokens, output_tokens, total_tokens "
            "FROM zeno_keys ORDER BY id DESC",
            (),
            "all",
        )
        cols = ("id", "name", "email", "created_at", "last_used_at", "revoked_at", "requests", "audio_seconds", "input_tokens", "output_tokens", "total_tokens")
        return [dict(zip(cols, r)) for r in rows]

    def list_runs(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._run(
            """SELECT id, key_id, editor_name, project, language, agent_name, agent_model,
                      clips_count, video_seconds, transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                      agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens,
                      clips_json, created_at
               FROM zeno_runs ORDER BY id DESC LIMIT %s""",
            (int(limit),),
            "all",
        )
        cols = (
            "id", "key_id", "editor_name", "project", "language", "agent_name", "agent_model",
            "clips_count", "video_seconds", "transcribe_in_tokens", "transcribe_out_tokens", "transcribe_total_tokens",
            "agent_in_tokens", "agent_cached_tokens", "agent_out_tokens", "agent_total_tokens", "grand_total_tokens",
            "clips_json", "created_at",
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(zip(cols, r))
            try:
                item["clips"] = json.loads(item.pop("clips_json") or "[]")
            except Exception:
                item["clips"] = []
            out.append(item)
        return out

    def revoke(self, key_id: int) -> bool:
        before = self._run("SELECT COUNT(*) FROM zeno_keys WHERE id = %s AND revoked_at IS NULL", (key_id,), "one")[0]
        if before:
            self._run("UPDATE zeno_keys SET revoked_at = %s WHERE id = %s", (time.time(), key_id))
        return bool(before)
