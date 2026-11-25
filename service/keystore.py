"""Personal-key, Google OAuth SSO, and editorial learning store for the Zeno service.

Postgres when DATABASE_URL is set (Railway), otherwise a local SQLite file so the service runs and tests on a laptop.
Only a SHA-256 hash of each key is stored, so keys can't be read back, not even by the owner.
Stores per-clip Gemini transcription token counts, full per-job Agent + Clip token telemetry,
and structured editorial learnings (`initial_edl`, `final_edl`, `edl_diff_json`, `learned_rules_json`, `assets_used_json`)
in `zeno_runs` and `zeno_learned_rules`.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _digest(payload: Dict[str, Any], editor: str, created_at: float, updated_at: float) -> str:
    """The job's small summary (patterns.py) as JSON; empty when it cannot be made (never stops a record being stored)."""
    try:
        import patterns
        return json.dumps(patterns.digest(payload, editor, created_at, updated_at), ensure_ascii=False)
    except Exception:
        return ""

MAX_ACTIVE_KEYS_PER_EMAIL = int(os.getenv("ZENO_MAX_KEYS_PER_EMAIL", "3"))
GOOGLE_MAX_ACTIVE_KEYS_PER_EMAIL = int(os.getenv("ZENO_GOOGLE_MAX_KEYS_PER_EMAIL", "50"))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def rule_text_hash(text: Any) -> str:
    """The identity of a team rule: its text, lower-cased with spaces collapsed. The Zeno client computes the same value."""
    norm = " ".join(str(text or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


class KeyStore:
    def __init__(self, database_url: Optional[str] = None, sqlite_path: Optional[str] = None):
        self.url = database_url or ""
        self.postgres = self.url.startswith(("postgres://", "postgresql://"))
        self.lock = threading.Lock()
        self._pg_conn = None
        if self.postgres:
            import psycopg  # imported only when needed, so SQLite-only use has no dependency
            self._psycopg = psycopg
            self.backend = "postgres"
        else:
            path = sqlite_path or os.getenv("ZENO_DB_PATH") or str(Path(__file__).resolve().parent / "zeno_keys.db")
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.backend = f"sqlite:{path}"
        self._init_schema()

    def _get_pg_conn(self):
        if self._pg_conn is not None and not getattr(self._pg_conn, "closed", True):
            return self._pg_conn
        self._pg_conn = self._psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        return self._pg_conn

    # -- plumbing ----------------------------------------------------------------------------
    def _run(self, sql: str, params: tuple = (), fetch: str = "none") -> Any:
        """Run one statement. SQL is written with %s placeholders for both backends."""
        with self.lock:
            if self.postgres:
                for attempt in range(2):
                    try:
                        conn = self._get_pg_conn()
                        cur = conn.execute(sql, params)
                        return cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else None
                    except Exception:
                        try:
                            if self._pg_conn is not None:
                                self._pg_conn.close()
                        except Exception:
                            pass
                        self._pg_conn = None
                        if attempt == 1:
                            raise
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
            user_email TEXT NOT NULL DEFAULT '',
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
            initial_edl TEXT NOT NULL DEFAULT '',
            final_edl TEXT NOT NULL DEFAULT '',
            edl_diff_json TEXT NOT NULL DEFAULT '{{}}',
            learned_rules_json TEXT NOT NULL DEFAULT '[]',
            assets_used_json TEXT NOT NULL DEFAULT '{{}}',
            created_at {real} NOT NULL)""")

        run_migrations = (
            ("user_email", "TEXT NOT NULL DEFAULT ''"),
            ("initial_edl", "TEXT NOT NULL DEFAULT ''"),
            ("final_edl", "TEXT NOT NULL DEFAULT ''"),
            ("edl_diff_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("learned_rules_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("assets_used_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("job_report_json", "TEXT NOT NULL DEFAULT '{}'"),
        )
        for col, col_def in run_migrations:
            try:
                if self.postgres:
                    self._run(f"ALTER TABLE zeno_runs ADD COLUMN IF NOT EXISTS {col} {col_def}")
                else:
                    self._run(f"ALTER TABLE zeno_runs ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        rule_pk = "SERIAL PRIMARY KEY" if self.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_learned_rules (
            id {rule_pk},
            rule_id TEXT UNIQUE,
            category TEXT,
            rule_text TEXT,
            lang TEXT DEFAULT 'all',
            author_email TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        # A rule's text is its identity (text_hash), so the same rule cannot be stored twice under different ids; a retired rule
        # (retired_at) is hidden from every PC and cannot be pushed back by a PC that still holds an old copy.
        for col, col_def in (("text_hash", "TEXT"), ("retired_at", real), ("source", "TEXT DEFAULT ''")):
            try:
                if self.postgres:
                    self._run(f"ALTER TABLE zeno_learned_rules ADD COLUMN IF NOT EXISTS {col} {col_def}")
                else:
                    self._run(f"ALTER TABLE zeno_learned_rules ADD COLUMN {col} {col_def}")
            except Exception:
                pass
        try:
            for rid, text in self._run("SELECT id, rule_text FROM zeno_learned_rules WHERE text_hash IS NULL", (), "all") or []:
                self._run("UPDATE zeno_learned_rules SET text_hash = %s WHERE id = %s", (rule_text_hash(text), rid))
        except Exception:
            pass

        # Tables added later live in their own step: if one cannot be created (for example on a database that rejects the
        # statement) the service still starts, signs people in and transcribes; only that feature reports an error.
        try:
            self._init_optional_schema(real, rule_pk)
        except Exception:
            import logging

            logging.getLogger("zeno.service").exception("optional tables could not be created; those features are off")

    def _init_optional_schema(self, real: str, rule_pk: str) -> None:
        # Transcripts by audio hash (a retry or a second editor on the same clip costs nothing), and per-job format observations
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_transcript_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            created_at {real} NOT NULL)""")
        # One row per transcription call: who, which model answered, which failed, how long, and why it failed. Numbers and short
        # reasons only: no audio, no transcript text.
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_transcriptions (
            id {rule_pk},
            key_id INTEGER,
            user_name TEXT NOT NULL DEFAULT '',
            created_at {real} NOT NULL,
            status TEXT NOT NULL,
            http_status INTEGER NOT NULL DEFAULT 200,
            model TEXT NOT NULL DEFAULT '',
            failed_models TEXT NOT NULL DEFAULT '',
            words INTEGER NOT NULL DEFAULT 0,
            audio_seconds {real} NOT NULL DEFAULT 0,
            bytes INTEGER NOT NULL DEFAULT 0,
            seconds {real} NOT NULL DEFAULT 0,
            in_tokens INTEGER NOT NULL DEFAULT 0,
            out_tokens INTEGER NOT NULL DEFAULT 0,
            cached INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '')""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_format_observations (
            id {rule_pk},
            key_id INTEGER,
            style TEXT NOT NULL,
            script_hash TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            video_seconds {real} NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL,
            created_at {real} NOT NULL)""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_format_recipes (
            style TEXT NOT NULL,
            fmt TEXT NOT NULL,
            stats_json TEXT NOT NULL,
            n_jobs INTEGER NOT NULL,
            approved_by TEXT NOT NULL,
            approved_at {real} NOT NULL,
            PRIMARY KEY (style, fmt))""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_knowledge (
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            match_text TEXT NOT NULL DEFAULT '',
            embedding_json TEXT,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at {real} NOT NULL,
            PRIMARY KEY (kind, name))""")
        blob_type = "BYTEA" if self.postgres else "BLOB"
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_releases (
            id {rule_pk},
            version TEXT NOT NULL,
            sha TEXT NOT NULL DEFAULT '',
            bundle_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            mandatory INTEGER NOT NULL DEFAULT 0,
            dmg_url TEXT NOT NULL DEFAULT '',
            exe_url TEXT NOT NULL DEFAULT '',
            published_by TEXT NOT NULL DEFAULT '',
            created_at {real} NOT NULL,
            bundle_zip {blob_type})""")
        # The whole story of one edit, end to end (script, every version of the plan, checks, verification, editor feedback),
        # kept so the team can later study where Zeno's editing fell short. One row per job; a resend replaces its own row.
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_job_archive (
            id {rule_pk},
            job_uid TEXT NOT NULL UNIQUE,
            key_id INTEGER,
            editor_name TEXT NOT NULL DEFAULT '',
            user_email TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            video_name TEXT NOT NULL DEFAULT '',
            zeno_version TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'render',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at {real} NOT NULL,
            updated_at {real} NOT NULL,
            payload_json TEXT NOT NULL)""")
        # One row per delivered video: its input (Notion name, footage path, script), its output (the delivery name), and the
        # edit data (final plan, first plan, decisions, editor corrections). Text only, never audio or video. One row per job_uid,
        # so a re-render updates its own row; the same output delivered from a different job is flagged, not dropped.
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_edits (
            id {rule_pk},
            job_uid TEXT NOT NULL UNIQUE,
            key_id INTEGER,
            editor_name TEXT NOT NULL DEFAULT '',
            user_email TEXT NOT NULL DEFAULT '',
            notion_name TEXT NOT NULL DEFAULT '',
            footage_path TEXT NOT NULL DEFAULT '',
            script_url TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            output_name TEXT NOT NULL DEFAULT '',
            output_key TEXT NOT NULL DEFAULT '',
            video_seconds {real} NOT NULL DEFAULT 0,
            revisions INTEGER NOT NULL DEFAULT 1,
            flag_level TEXT NOT NULL DEFAULT '',
            flag_code TEXT NOT NULL DEFAULT '',
            flag_reason TEXT NOT NULL DEFAULT '',
            duplicate_of INTEGER,
            zeno_version TEXT NOT NULL DEFAULT '',
            delivered_at {real} NOT NULL,
            updated_at {real} NOT NULL,
            edit_json TEXT NOT NULL DEFAULT '{{}}')""")
        try:
            self._run("CREATE INDEX IF NOT EXISTS zeno_edits_output ON zeno_edits (output_key)")
            self._run("CREATE INDEX IF NOT EXISTS zeno_edits_delivered ON zeno_edits (delivered_at)")
        except Exception:
            pass
        # Which Zeno version each editor runs, learned from every request that says it and from every update download, plus
        # the history of each change (who moved to which version, when, and how we know).
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_version_events (
            id {rule_pk},
            key_id INTEGER,
            editor_name TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            created_at {real} NOT NULL)""")
        for table, col, col_def in (
            ("zeno_job_archive", "digest_json", "TEXT NOT NULL DEFAULT ''"),
            ("zeno_keys", "zeno_version", "TEXT NOT NULL DEFAULT ''"),
            ("zeno_keys", "version_seen_at", f"{real}"),
            ("zeno_keys", "client_platform", "TEXT NOT NULL DEFAULT ''"),
            ("zeno_transcriptions", "zeno_version", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                if self.postgres:
                    self._run(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_def}")
                else:
                    self._run(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        # Unified FastAPI video tracker tables (v3.0.3): one canonical row per video job, immutable completion date,
        # idempotent event log, per-machine fleet state, and verified post-install OTA update receipts.
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_video_jobs (
            id {rule_pk},
            job_key TEXT NOT NULL UNIQUE,
            editor_email TEXT NOT NULL DEFAULT '',
            editor_name TEXT NOT NULL DEFAULT '',
            machine_id TEXT NOT NULL DEFAULT '',
            notion_name TEXT NOT NULL DEFAULT '',
            canonical_name TEXT NOT NULL DEFAULT '',
            script_source TEXT NOT NULL DEFAULT '',
            script_id TEXT NOT NULL DEFAULT '',
            footage_path TEXT NOT NULL DEFAULT '',
            project_name TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'auto',
            zeno_version TEXT NOT NULL DEFAULT '',
            client_platform TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            last_stage TEXT NOT NULL DEFAULT 'open',
            opened_at {real} NOT NULL,
            first_rendered_at {real},
            first_completed_at {real},
            first_completed_date TEXT NOT NULL DEFAULT '',
            last_event_at {real} NOT NULL,
            total_duration_sec {real} NOT NULL DEFAULT 0,
            unique_clips_count INTEGER NOT NULL DEFAULT 0,
            seen_clip_hashes_json TEXT NOT NULL DEFAULT '[]',
            gemini_calls INTEGER NOT NULL DEFAULT 0,
            gemini_retry_calls INTEGER NOT NULL DEFAULT 0,
            gemini_in_tokens INTEGER NOT NULL DEFAULT 0,
            gemini_out_tokens INTEGER NOT NULL DEFAULT 0,
            gemini_retry_in_tokens INTEGER NOT NULL DEFAULT 0,
            gemini_retry_out_tokens INTEGER NOT NULL DEFAULT 0,
            gemini_audio_seconds {real} NOT NULL DEFAULT 0,
            agent_name TEXT NOT NULL DEFAULT '',
            agent_model TEXT NOT NULL DEFAULT '',
            agent_calls INTEGER NOT NULL DEFAULT 0,
            agent_in_tokens INTEGER NOT NULL DEFAULT 0,
            agent_cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            agent_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            agent_out_tokens INTEGER NOT NULL DEFAULT 0,
            agent_total_tokens INTEGER NOT NULL DEFAULT 0,
            user_prompt_count INTEGER NOT NULL DEFAULT 0,
            user_prompts_json TEXT NOT NULL DEFAULT '[]',
            render_count INTEGER NOT NULL DEFAULT 0,
            revision_count INTEGER NOT NULL DEFAULT 0,
            revisions_json TEXT NOT NULL DEFAULT '[]',
            output_folder TEXT NOT NULL DEFAULT '',
            output_files_json TEXT NOT NULL DEFAULT '[]',
            error_count INTEGER NOT NULL DEFAULT 0,
            block_count INTEGER NOT NULL DEFAULT 0,
            last_error_category TEXT NOT NULL DEFAULT '',
            last_error_message TEXT NOT NULL DEFAULT '',
            last_error_at {real})""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_track_events (
            id {rule_pk},
            event_id TEXT NOT NULL UNIQUE,
            job_key TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            editor_email TEXT NOT NULL DEFAULT '',
            editor_name TEXT NOT NULL DEFAULT '',
            machine_id TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            error_category TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{{}}',
            client_ts {real} NOT NULL,
            created_at {real} NOT NULL)""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_client_machines (
            editor_email TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            editor_name TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            zeno_version TEXT NOT NULL DEFAULT '',
            verified_version TEXT NOT NULL DEFAULT '',
            bundle_sha256 TEXT NOT NULL DEFAULT '',
            auth_source TEXT NOT NULL DEFAULT '',
            pending_outbox INTEGER NOT NULL DEFAULT 0,
            last_update_status TEXT NOT NULL DEFAULT '',
            last_update_error TEXT NOT NULL DEFAULT '',
            last_seen_at {real} NOT NULL,
            PRIMARY KEY (editor_email, machine_id))""")
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_update_receipts (
            id {rule_pk},
            event_id TEXT NOT NULL UNIQUE,
            editor_email TEXT NOT NULL DEFAULT '',
            editor_name TEXT NOT NULL DEFAULT '',
            machine_id TEXT NOT NULL DEFAULT '',
            before_version TEXT NOT NULL DEFAULT '',
            after_version TEXT NOT NULL DEFAULT '',
            bundle_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'installed',
            platform TEXT NOT NULL DEFAULT '',
            files_updated INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            created_at {real} NOT NULL)""")
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS zeno_vjobs_ed_last ON zeno_video_jobs (editor_email, last_event_at)",
            "CREATE INDEX IF NOT EXISTS zeno_vjobs_done_day ON zeno_video_jobs (first_completed_date)",
            "CREATE INDEX IF NOT EXISTS zeno_vjobs_ed_cname ON zeno_video_jobs (editor_email, canonical_name)",
            "CREATE INDEX IF NOT EXISTS zeno_vjobs_ed_sid ON zeno_video_jobs (editor_email, script_id)",
            "CREATE INDEX IF NOT EXISTS zeno_tev_job_ts ON zeno_track_events (job_key, client_ts)",
            "CREATE INDEX IF NOT EXISTS zeno_tev_created ON zeno_track_events (created_at)",
            "CREATE INDEX IF NOT EXISTS zeno_tev_err ON zeno_track_events (error_category)",
        ):
            try:
                self._run(idx_sql)
            except Exception:
                pass

    # -- operations --------------------------------------------------------------------------
    def create(self, name: str, email: str) -> str:
        """Make a key for this person and return it. It is shown once and never stored in readable form."""
        name, email = name.strip(), email.strip().lower()
        max_keys = int(os.getenv("ZENO_MAX_KEYS_PER_EMAIL", str(MAX_ACTIVE_KEYS_PER_EMAIL)))
        active = self._run("SELECT COUNT(*) FROM zeno_keys WHERE email = %s AND revoked_at IS NULL", (email,), "one")[0]
        if active >= max_keys:
            raise ValueError(f"This email already has {active} active keys. Ask the owner to revoke one first.")
        key = "zeno_" + secrets.token_urlsafe(32)
        self._run(
            "INSERT INTO zeno_keys (name, email, key_hash, created_at) VALUES (%s, %s, %s, %s)",
            (name, email, hash_key(key), time.time()),
        )
        return key

    def create_or_get_google_key(self, name: str, email: str, google_sub: str = "") -> Dict[str, Any]:
        """Issue a fresh session key for a Google-authenticated user, rotating oldest key only above high cap."""
        name, email = (name or "").strip() or email.split("@")[0], email.strip().lower()
        active_rows = self._run(
            "SELECT id FROM zeno_keys WHERE email = %s AND revoked_at IS NULL ORDER BY created_at ASC",
            (email,),
            "all",
        ) or []
        while len(active_rows) >= GOOGLE_MAX_ACTIVE_KEYS_PER_EMAIL and active_rows:
            oldest_id = int(active_rows.pop(0)[0])
            self.revoke(oldest_id)
        key = "zeno_" + secrets.token_urlsafe(32)
        now = time.time()
        self._run(
            "INSERT INTO zeno_keys (name, email, key_hash, created_at) VALUES (%s, %s, %s, %s)",
            (name, email, hash_key(key), now),
        )
        row = self._run("SELECT id FROM zeno_keys WHERE key_hash = %s", (hash_key(key),), "one")
        key_id = int(row[0]) if row else 0
        return {
            "key": key,
            "prefix": key[:12],
            "key_id": key_id,
            "name": name,
            "email": email,
        }

    def lookup(self, key: str) -> Optional[Tuple[int, str]]:
        """(id, name) for an active key, else None."""
        row = self._run("SELECT id, name FROM zeno_keys WHERE key_hash = %s AND revoked_at IS NULL", (hash_key(key),), "one")
        return (int(row[0]), row[1]) if row else None

    def get_key_email(self, key_id: int) -> str:
        row = self._run("SELECT email FROM zeno_keys WHERE id = %s", (int(key_id),), "one")
        return str(row[0] or "").strip().lower() if row else ""

    def record_use(self, key_id: int, audio_seconds: float, input_tokens: int = 0, output_tokens: int = 0, total_tokens: int = 0) -> None:
        tot = int(total_tokens or (int(input_tokens) + int(output_tokens)))
        self._run(
            "UPDATE zeno_keys SET last_used_at = %s, requests = requests + 1, audio_seconds = audio_seconds + %s, "
            "input_tokens = input_tokens + %s, output_tokens = output_tokens + %s, total_tokens = total_tokens + %s WHERE id = %s",
            (time.time(), float(audio_seconds), int(input_tokens), int(output_tokens), tot, key_id),
        )

    @staticmethod
    def _to_json_str(val: Any, default_str: str) -> str:
        if val is None:
            return default_str
        if isinstance(val, str):
            s = val.strip()
            return s if s else default_str
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return default_str

    def log_run(self, key_id: Optional[int], payload: Dict[str, Any], editor_name: str = "") -> int:
        payload = payload or {}
        resolved_name = str(editor_name or payload.get("editor_name") or "").strip()
        user_email = str(payload.get("user_email") or payload.get("author_email") or "").strip().lower()

        if key_id is not None and (not user_email or not resolved_name):
            row = self._run("SELECT name, email FROM zeno_keys WHERE id = %s", (key_id,), "one")
            if row:
                if not resolved_name:
                    resolved_name = str(row[0] or "")
                if not user_email:
                    user_email = str(row[1] or "").strip().lower()

        clips = payload.get("clips") if isinstance(payload.get("clips"), list) else []
        t_in = int(payload.get("transcribe_in_tokens") or 0)
        t_out = int(payload.get("transcribe_out_tokens") or 0)
        raw_t_tot = int(payload.get("transcribe_total_tokens") or 0)
        t_tot = raw_t_tot if raw_t_tot > 0 else (t_in + t_out)
        a_in = int(payload.get("agent_in_tokens") or 0)
        a_cache = int(payload.get("agent_cached_tokens") or 0)
        a_out = int(payload.get("agent_out_tokens") or 0)
        raw_a_tot = int(payload.get("agent_total_tokens") or 0)
        a_tot = raw_a_tot if raw_a_tot > 0 else (a_in + a_out)
        raw_grand = int(payload.get("grand_total_tokens") or 0)
        agent_with_cache = (a_in + a_out + a_cache) if (a_in or a_out) else (a_tot + a_cache)
        grand = raw_grand if raw_grand > 0 else (agent_with_cache + t_tot)

        initial_edl = str(payload.get("initial_edl") or "")
        final_edl = str(payload.get("final_edl") or "")
        edl_diff_raw = payload.get("edl_diff") if "edl_diff" in payload else payload.get("edl_diff_json")
        edl_diff_json = self._to_json_str(edl_diff_raw, "{}")

        learned_raw = payload.get("learned_rules") if "learned_rules" in payload else payload.get("learned_rules_json")
        if isinstance(learned_raw, str):
            try:
                learned_list = json.loads(learned_raw)
                if not isinstance(learned_list, list):
                    learned_list = []
            except Exception:
                learned_list = []
        elif isinstance(learned_raw, list):
            learned_list = learned_raw
        else:
            learned_list = []
        learned_rules_json = self._to_json_str(learned_list, "[]")

        assets_raw = payload.get("assets_used") if "assets_used" in payload else payload.get("assets_used_json")
        assets_used_json = self._to_json_str(assets_raw, "{}")

        jr_raw = payload.get("job_report")
        if isinstance(jr_raw, dict):
            jr = dict(jr_raw)
            cw_top = int(payload.get("agent_cache_write_tokens") or payload.get("cache_write_tokens") or 0)
            e1h_top = int(payload.get("ephemeral_1h_input_tokens") or 0)
            if cw_top > 0 or e1h_top > 0:
                acc = dict(jr.get("accounting") or {}) if isinstance(jr.get("accounting"), dict) else {}
                if cw_top > 0 and "cache_write_tokens" not in acc:
                    acc["cache_write_tokens"] = cw_top
                if e1h_top > 0 and "ephemeral_1h_input_tokens" not in acc:
                    acc["ephemeral_1h_input_tokens"] = e1h_top
                jr["accounting"] = acc
            job_report_json = json.dumps(jr, ensure_ascii=False)[:20000]
        else:
            job_report_json = "{}"

        now = time.time()
        proj_str = str(payload.get("project") or "unnamed")[:120]
        editor_str = str(resolved_name or "unknown")[:60]
        email_str = user_email[:120]
        new_sess = ""
        if isinstance(jr_raw, dict):
            new_sess = str(((jr_raw.get("accounting") or {}).get("agent_session_id")) or "").strip()
        if not new_sess:
            new_sess = str(payload.get("agent_session_id") or "").strip()
        if new_sess in ("local", "unknown"):
            new_sess = ""

        # Deduplicate re-render checkpoints of the same active edit so 5 re-renders in one session update 1 row instead of 5x summing
        existing_id: Optional[int] = None
        if proj_str and proj_str != "unnamed":
            cutoff = now - 6 * 3600.0
            if email_str:
                prev_row = self._run(
                    "SELECT id, agent_cached_tokens, job_report_json FROM zeno_runs WHERE user_email = %s AND project = %s AND created_at >= %s ORDER BY id DESC LIMIT 1",
                    (email_str, proj_str, cutoff),
                    "one",
                )
            elif key_id is not None:
                prev_row = self._run(
                    "SELECT id, agent_cached_tokens, job_report_json FROM zeno_runs WHERE key_id = %s AND project = %s AND created_at >= %s ORDER BY id DESC LIMIT 1",
                    (key_id, proj_str, cutoff),
                    "one",
                )
            else:
                prev_row = None
            if prev_row:
                cand_id = int(prev_row[0] or 0)
                old_cache = int(prev_row[1] or 0)
                old_sess = ""
                try:
                    ojr = json.loads(prev_row[2] or "{}")
                    old_sess = str(((ojr.get("accounting") or {}).get("agent_session_id")) or "").strip()
                    if old_sess in ("local", "unknown"):
                        old_sess = ""
                except Exception:
                    old_sess = ""
                if new_sess and old_sess:
                    if new_sess == old_sess:
                        existing_id = cand_id
                elif a_cache >= old_cache:
                    existing_id = cand_id

        if existing_id:
            self._run(
                """UPDATE zeno_runs SET
                    key_id = %s, editor_name = %s, user_email = %s, language = %s, agent_name = %s, agent_model = %s,
                    clips_count = %s, video_seconds = %s, transcribe_in_tokens = %s, transcribe_out_tokens = %s, transcribe_total_tokens = %s,
                    agent_in_tokens = %s, agent_cached_tokens = %s, agent_out_tokens = %s, agent_total_tokens = %s, grand_total_tokens = %s,
                    clips_json = %s, initial_edl = %s, final_edl = %s, edl_diff_json = %s, learned_rules_json = %s, assets_used_json = %s,
                    job_report_json = %s, created_at = %s
                WHERE id = %s""",
                (
                    key_id, editor_str, email_str,
                    str(payload.get("language") or "auto")[:20],
                    str(payload.get("agent_name") or "unknown")[:60],
                    str(payload.get("agent_model") or "unknown")[:80],
                    int(payload.get("clips_count") or len(clips)),
                    float(payload.get("video_seconds") or 0.0),
                    t_in, t_out, t_tot,
                    a_in, a_cache, a_out, a_tot, grand,
                    json.dumps(clips[:60], ensure_ascii=False),
                    initial_edl, final_edl, edl_diff_json, learned_rules_json, assets_used_json,
                    job_report_json, now,
                    existing_id,
                ),
            )
            run_id = existing_id
        else:
            self._run(
                """INSERT INTO zeno_runs (
                    key_id, editor_name, user_email, project, language, agent_name, agent_model,
                    clips_count, video_seconds, transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                    agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens,
                    clips_json, initial_edl, final_edl, edl_diff_json, learned_rules_json, assets_used_json, job_report_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    key_id,
                    editor_str,
                    email_str,
                    proj_str,
                    str(payload.get("language") or "auto")[:20],
                    str(payload.get("agent_name") or "unknown")[:60],
                    str(payload.get("agent_model") or "unknown")[:80],
                    int(payload.get("clips_count") or len(clips)),
                    float(payload.get("video_seconds") or 0.0),
                    t_in, t_out, t_tot,
                    a_in, a_cache, a_out, a_tot, grand,
                    json.dumps(clips[:60], ensure_ascii=False),
                    initial_edl,
                    final_edl,
                    edl_diff_json,
                    learned_rules_json,
                    assets_used_json,
                    job_report_json,
                    now,
                ),
            )
            row = self._run("SELECT MAX(id) FROM zeno_runs", (), "one")
            run_id = int(row[0] or 0)

        run_lang = str(payload.get("language") or "all").strip().lower() or "all"
        for item in learned_list:
            if isinstance(item, str) and item.strip():
                rule_obj: Dict[str, Any] = {"rule_text": item.strip(), "lang": run_lang}
            elif isinstance(item, dict):
                rule_obj = dict(item)
                if not rule_obj.get("lang") and not rule_obj.get("language"):
                    rule_obj["lang"] = run_lang
            else:
                continue
            try:
                self.add_learned_rule(rule_obj, author_email=user_email)
            except Exception:
                pass

        return run_id

    # -- job archive: the whole edit, end to end -----------------------------------------------
    def record_job(self, key_id: Optional[int], editor_name: str, payload: Dict[str, Any]) -> int:
        """Store (or replace) one job's full record. `payload["job_uid"]` identifies the job, so a resend never duplicates."""
        payload = payload or {}
        uid = str(payload.get("job_uid") or "").strip()[:120]
        if not uid:
            raise ValueError("job_uid is required")
        email = ""
        if key_id is not None:
            row = self._run("SELECT name, email FROM zeno_keys WHERE id = %s", (key_id,), "one")
            if row:
                editor_name = editor_name or str(row[0] or "")
                email = str(row[1] or "").lower()
        body = json.dumps(payload, ensure_ascii=False)
        now = time.time()
        old = self._run("SELECT created_at FROM zeno_job_archive WHERE job_uid = %s", (uid,), "one")
        digest = _digest(payload, str(editor_name or ""), float(old[0]) if old else now, now)
        self._run(
            """INSERT INTO zeno_job_archive (job_uid, key_id, editor_name, user_email, project, video_name, zeno_version, kind,
                                             size_bytes, created_at, updated_at, payload_json, digest_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (job_uid) DO UPDATE SET
                   payload_json = EXCLUDED.payload_json, size_bytes = EXCLUDED.size_bytes, updated_at = EXCLUDED.updated_at,
                   video_name = EXCLUDED.video_name, zeno_version = EXCLUDED.zeno_version, kind = EXCLUDED.kind,
                   digest_json = EXCLUDED.digest_json""",
            (uid, key_id, str(editor_name or "")[:120], email[:160], str(payload.get("project") or "")[:200],
             str(payload.get("video_name") or "")[:300], str(payload.get("zeno_version") or "")[:40],
             str(payload.get("kind") or "render")[:30], len(body.encode("utf-8")), now, now, body, digest),
        )
        if payload.get("zeno_version"):
            self.note_version(key_id, str(payload["zeno_version"]), str(payload.get("platform") or ""), "job record", editor_name)
        try:
            if str(payload.get("kind") or "render") == "render":
                self.record_edit(key_id, str(editor_name or ""), email, payload)
        except Exception:
            pass   # the edit log must never stop a job record from being stored
        row = self._run("SELECT id FROM zeno_job_archive WHERE job_uid = %s", (uid,), "one")
        return int(row[0]) if row else 0

    # -- the edit log: one row per delivered video ------------------------------------------------
    _IST = timezone(timedelta(hours=5, minutes=30))
    EDIT_DATA_MAX = 600_000

    @staticmethod
    def _output_key(name: str) -> str:
        """The identity of an output: its delivery name, case and spacing ignored."""
        n = " ".join(str(name or "").replace("reel_", "", 1).replace("SQ_", "", 1).split()).lower()
        return n[:300]

    @staticmethod
    def _plan_header(plan_text: str, tag: str) -> str:
        m = re.search(r"^#" + tag + r'\s+"?([^"\n]+?)"?\s*$', plan_text or "", re.M)
        return m.group(1).strip() if m else ""

    def record_edit(self, key_id: Optional[int], editor_name: str, email: str, payload: Dict[str, Any]) -> int:
        """Log one delivered video. A resend of the same job updates its row; the same output arriving from another job is flagged."""
        uid = str(payload.get("job_uid") or "").strip()[:120]
        if not uid:
            return 0
        plan_final = str(payload.get("plan_final") or "")
        output_name = str(payload.get("video_name") or "").strip()
        if not output_name:
            return 0
        footage = str(payload.get("footage_path") or "") or self._plan_header(plan_final, "src_dir")
        script_url = str(payload.get("script_url") or payload.get("script_source") or "") or self._plan_header(plan_final, "script")
        notion = str(payload.get("notion_name") or payload.get("task_name") or "").strip() or output_name
        versions = payload.get("plan_versions") if isinstance(payload.get("plan_versions"), list) else []
        first_plan = str((versions[0] or {}).get("text") or "") if versions and isinstance(versions[0], dict) else ""
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        data = {
            "plan_final": plan_final, "plan_first": first_plan,
            "plan_history": payload.get("plan_history") or [],
            "decisions": payload.get("decisions") or "",
            "script": payload.get("script") or "",
            "script_analysis": payload.get("script_analysis") or "",
            "feedback": payload.get("feedback") or [],
            "summary": summary,
        }
        body = json.dumps(data, ensure_ascii=False)
        if len(body) > self.EDIT_DATA_MAX:   # the plans and the script matter most: drop the bulky extras first
            data["plan_history"], data["script_analysis"] = [], ""
            body = json.dumps(data, ensure_ascii=False)
        if len(body) > self.EDIT_DATA_MAX:
            data["plan_first"] = ""
            body = json.dumps(data, ensure_ascii=False)
        now = time.time()
        okey = self._output_key(output_name)
        email = (email or "").lower()[:160]
        old = self._run("SELECT id, delivered_at, revisions FROM zeno_edits WHERE job_uid = %s", (uid,), "one")
        if old:
            self._run(
                """UPDATE zeno_edits SET key_id=%s, editor_name=%s, user_email=%s, notion_name=%s, footage_path=%s, script_url=%s,
                       project=%s, output_name=%s, output_key=%s, video_seconds=%s, revisions=%s, zeno_version=%s, updated_at=%s,
                       edit_json=%s WHERE job_uid=%s""",
                (key_id, editor_name[:120], email, notion[:300], footage[:500], script_url[:500], str(payload.get("project") or "")[:200],
                 output_name[:300], okey, float(summary.get("video_seconds") or 0.0), int(old[2] or 1) + 1,
                 str(payload.get("zeno_version") or "")[:40], now, body, uid),
            )
            edit_id = int(old[0])
        else:
            self._run(
                """INSERT INTO zeno_edits (job_uid, key_id, editor_name, user_email, notion_name, footage_path, script_url, project,
                       output_name, output_key, video_seconds, zeno_version, delivered_at, updated_at, edit_json)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (uid, key_id, editor_name[:120], email, notion[:300], footage[:500], script_url[:500], str(payload.get("project") or "")[:200],
                 output_name[:300], okey, float(summary.get("video_seconds") or 0.0), str(payload.get("zeno_version") or "")[:40], now, now, body),
            )
            row = self._run("SELECT id FROM zeno_edits WHERE job_uid = %s", (uid,), "one")
            edit_id = int(row[0]) if row else 0
        self._flag_edit(edit_id)
        return edit_id

    def _flag_edit(self, edit_id: int) -> None:
        """Mark an edit when its output was already delivered by another job (error or warning, with the reason),
        or when its script already produced a different output."""
        me = self._run(
            "SELECT id, job_uid, editor_name, user_email, footage_path, script_url, output_name, output_key, delivered_at FROM zeno_edits WHERE id = %s",
            (edit_id,), "one")
        if not me:
            return
        _id, uid, editor, email, footage, script, out_name, okey, delivered = me
        first = self._run(
            """SELECT id, editor_name, user_email, footage_path, script_url, delivered_at FROM zeno_edits
               WHERE output_key = %s AND job_uid <> %s AND delivered_at <= %s ORDER BY delivered_at, id LIMIT 1""",
            (okey, uid, delivered), "one")
        level = code = reason = ""
        dup_of: Optional[int] = None
        if first:
            f_id, f_editor, f_email, f_footage, f_script, f_at = first
            when = datetime.fromtimestamp(float(f_at), self._IST).strftime("%d %b %H:%M IST")
            n = int((self._run("SELECT COUNT(*) FROM zeno_edits WHERE output_key = %s", (okey,), "one") or [1])[0])
            diffs = []
            if (f_email or "") != (email or ""):
                diffs.append(f"a different editor ({f_editor or f_email} vs {editor or email})")
            if f_script and script and f_script != script:
                diffs.append("a different script")
            if f_footage and footage and f_footage != footage:
                diffs.append("a different footage folder")
            if diffs:
                level, code = "error", "DUPLICATE_OUTPUT_DIFFERENT_INPUT"
                reason = (f"Output '{out_name}' was already delivered (edit #{f_id}, {when}) from {' and '.join(diffs)}. "
                          f"Two videos share one output name, so one delivery overwrites the other. This is delivery {n} of this name.")
            else:
                level, code = "warning", "DUPLICATE_OUTPUT_SAME_INPUT"
                reason = (f"Output '{out_name}' was already delivered (edit #{f_id}, {when}) from the same input. "
                          f"The video was delivered again from another project folder (delivery {n}); count it once.")
            dup_of = int(f_id)
        elif script and script.startswith("http"):
            other = self._run(
                "SELECT id, output_name FROM zeno_edits WHERE script_url = %s AND output_key <> %s AND job_uid <> %s ORDER BY delivered_at LIMIT 1",
                (script, okey, uid), "one")
            if other:
                level, code = "warning", "SAME_SCRIPT_TWO_OUTPUTS"
                reason = f"This script already produced a different output ('{other[1]}', edit #{other[0]}). One input should give one output."
                dup_of = int(other[0])
        self._run("UPDATE zeno_edits SET flag_level=%s, flag_code=%s, flag_reason=%s, duplicate_of=%s WHERE id=%s",
                  (level, code, reason, dup_of, edit_id))

    def list_edits(self, limit: int = 200, since: float = 0.0, editor: str = "", flagged_only: bool = False) -> List[Dict[str, Any]]:
        """The edit log, newest first, without the edit data."""
        where, params = ["delivered_at >= %s"], [float(since)]
        if editor:
            where.append("(LOWER(user_email) = %s OR LOWER(editor_name) = %s)")
            params += [editor.lower(), editor.lower()]
        if flagged_only:
            where.append("flag_level <> ''")
        rows = self._run(
            "SELECT id, job_uid, editor_name, user_email, notion_name, footage_path, script_url, project, output_name, video_seconds, "
            "revisions, flag_level, flag_code, flag_reason, duplicate_of, zeno_version, delivered_at, updated_at FROM zeno_edits "
            f"WHERE {' AND '.join(where)} ORDER BY delivered_at DESC LIMIT %s", tuple(params + [int(limit)]), "all")
        cols = ("id", "job_uid", "editor_name", "user_email", "notion_name", "footage_path", "script_url", "project", "output_name",
                "video_seconds", "revisions", "flag_level", "flag_code", "flag_reason", "duplicate_of", "zeno_version", "delivered_at", "updated_at")
        out = []
        for r in rows or []:
            d = dict(zip(cols, r))
            d["delivered_ist"] = datetime.fromtimestamp(float(d["delivered_at"]), self._IST).strftime("%Y-%m-%d %H:%M")
            out.append(d)
        return out

    def get_edit(self, edit_id: int) -> Optional[Dict[str, Any]]:
        """One edit with its edit data (plans, decisions, script, editor corrections)."""
        row = self._run("SELECT edit_json FROM zeno_edits WHERE id = %s", (int(edit_id),), "one")
        if not row:
            return None
        head = [e for e in self.list_edits(limit=100000, since=0.0) if e["id"] == int(edit_id)]
        try:
            data = json.loads(row[0] or "{}")
        except Exception:
            data = {}
        out = head[0] if head else {"id": int(edit_id)}
        out["edit"] = data
        return out

    def edit_stats(self, days: int = 14) -> Dict[str, Any]:
        """Per day (IST) and per editor: videos delivered (distinct outputs), deliveries, the times, and flags."""
        since = time.time() - max(1, int(days)) * 86400
        rows = self._run(
            "SELECT editor_name, user_email, output_key, delivered_at, flag_level FROM zeno_edits WHERE delivered_at >= %s ORDER BY delivered_at",
            (since,), "all") or []
        by_day: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for editor, email, okey, at, level in rows:
            ts = datetime.fromtimestamp(float(at), self._IST)
            who = (email or editor or "unknown").lower()
            e = by_day.setdefault(ts.strftime("%Y-%m-%d"), {}).setdefault(
                who, {"editor": editor or who, "email": email or "", "outputs": set(), "deliveries": 0, "times": [], "warnings": 0, "errors": 0})
            e["outputs"].add(okey)
            e["deliveries"] += 1
            e["times"].append(ts.strftime("%H:%M"))
            if level == "warning":
                e["warnings"] += 1
            elif level == "error":
                e["errors"] += 1
        days_out = []
        for day in sorted(by_day, reverse=True):
            eds = []
            for e in sorted(by_day[day].values(), key=lambda x: -len(x["outputs"])):
                eds.append({"editor": e["editor"], "email": e["email"], "videos": len(e["outputs"]), "deliveries": e["deliveries"],
                            "times": e["times"], "warnings": e["warnings"], "errors": e["errors"]})
            weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%a")
            days_out.append({"date": day, "weekday": weekday, "editors": eds, "editors_count": len(eds),
                             "videos": sum(x["videos"] for x in eds), "deliveries": sum(x["deliveries"] for x in eds)})
        return {"days": days_out, "timezone": "IST"}

    def backfill_edits(self, limit: int = 5000) -> int:
        """Fill the edit log from delivered jobs already in the archive (the history before the log existed). Safe to run again."""
        rows = self._run(
            "SELECT job_uid, key_id, editor_name, user_email, payload_json, created_at FROM zeno_job_archive WHERE kind = 'render' "
            "ORDER BY created_at LIMIT %s", (int(limit),), "all") or []
        done = 0
        for uid, key_id, editor, email, body, created in rows:
            if self._run("SELECT 1 FROM zeno_edits WHERE job_uid = %s", (uid,), "one"):
                continue
            try:
                payload = json.loads(body or "{}")
            except Exception:
                continue
            payload["job_uid"] = uid
            try:
                eid = self.record_edit(key_id, editor or "", email or "", payload)
                if eid:
                    self._run("UPDATE zeno_edits SET delivered_at = %s WHERE id = %s", (float(created), eid))
                    self._flag_edit(eid)
                    done += 1
            except Exception:
                continue
        return done

    def list_jobs(self, limit: int = 200, since: float = 0.0) -> List[Dict[str, Any]]:
        """The archive's index, newest first (no payloads)."""
        rows = self._run(
            """SELECT id, job_uid, editor_name, user_email, project, video_name, zeno_version, kind, size_bytes, created_at, updated_at
               FROM zeno_job_archive WHERE updated_at >= %s ORDER BY updated_at DESC LIMIT %s""",
            (float(since), int(limit)), "all",
        )
        cols = ("id", "job_uid", "editor_name", "user_email", "project", "video_name", "zeno_version", "kind", "size_bytes", "created_at", "updated_at")
        return [dict(zip(cols, r)) for r in rows or []]

    def job_digests(self, since: float = 0.0, limit: int = 5000) -> List[Dict[str, Any]]:
        """The small summary of every job (see patterns.py). Records stored before summaries existed get one now, once."""
        rows = self._run(
            """SELECT id, digest_json, editor_name, created_at, updated_at FROM zeno_job_archive
               WHERE updated_at >= %s ORDER BY updated_at DESC LIMIT %s""", (float(since), int(limit)), "all") or []
        out: List[Dict[str, Any]] = []
        try:
            import patterns
            current = patterns.DIGEST_VERSION
        except Exception:
            current = 0
        for job_id, dj, editor, created, updated in rows:
            d = None
            try:
                d = json.loads(dj) if dj else None
            except Exception:
                d = None
            if not d or d.get("v") != current:
                full = self._run("SELECT payload_json FROM zeno_job_archive WHERE id = %s", (job_id,), "one")
                try:
                    payload = json.loads(full[0]) if full else {}
                except Exception:
                    payload = {}
                dj = _digest(payload, str(editor or ""), float(created), float(updated))
                if dj:
                    self._run("UPDATE zeno_job_archive SET digest_json = %s WHERE id = %s", (dj, job_id))
                    d = json.loads(dj)
            if d:
                d["id"] = int(job_id)
                out.append(d)
        return out

    def note_version(self, key_id: Optional[int], version: str, platform: str = "", source: str = "", editor_name: str = "") -> None:
        """Remember the Zeno version this key is on; a change is also written to the version history."""
        version = str(version or "").strip()[:40]
        if key_id is None or not version:
            return
        row = self._run("SELECT zeno_version, name, client_platform FROM zeno_keys WHERE id = %s", (key_id,), "one")
        if not row:
            return
        now = time.time()
        clean_plat = (platform or "").strip()[:80]
        eff_plat = clean_plat or str(row[2] or "")[:80]
        self._run("UPDATE zeno_keys SET zeno_version = %s, version_seen_at = %s, client_platform = %s WHERE id = %s",
                  (version, now, eff_plat, key_id))
        if str(row[0] or "") != version:
            self._run("""INSERT INTO zeno_version_events (key_id, editor_name, version, source, platform, created_at)
                         VALUES (%s, %s, %s, %s, %s, %s)""",
                      (key_id, (editor_name or str(row[1] or ""))[:120], version, source[:60], eff_plat, now))

    def version_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self._run("""SELECT key_id, editor_name, version, source, platform, created_at FROM zeno_version_events
                            ORDER BY id DESC LIMIT %s""", (int(limit),), "all") or []
        cols = ("key_id", "editor_name", "version", "source", "platform", "created_at")
        return [dict(zip(cols, r)) for r in rows]

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        row = self._run("SELECT payload_json, editor_name, user_email, created_at, updated_at FROM zeno_job_archive WHERE id = %s", (int(job_id),), "one")
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except Exception:
            payload = {}
        return {"id": int(job_id), "editor_name": row[1], "user_email": row[2], "created_at": row[3], "updated_at": row[4], "payload": payload}

    def record_run(self, key_id: Optional[int], editor_name: str, payload: Dict[str, Any]) -> int:
        return self.log_run(key_id, payload, editor_name=editor_name)

    def add_learned_rule(self, rule_dict: Dict[str, Any], author_email: str = "") -> Dict[str, Any]:
        if not isinstance(rule_dict, dict):
            return {}
        category = str(rule_dict.get("category") or "general").strip()[:60] or "general"
        rule_text = str(rule_dict.get("rule_text") or rule_dict.get("text") or rule_dict.get("rule") or "").strip()
        if not rule_text:
            return {}
        lang = str(rule_dict.get("lang") or rule_dict.get("language") or "all").strip().lower()[:20] or "all"
        author = str(author_email or rule_dict.get("author_email") or rule_dict.get("user_email") or "").strip().lower()[:120]
        rule_id = str(rule_dict.get("rule_id") or rule_dict.get("id") or "").strip()[:80]
        if not rule_id:
            digest = hashlib.sha256(f"{category}:{lang}:{rule_text.lower()}".encode("utf-8")).hexdigest()[:16]
            rule_id = f"rule_{digest}"

        thash = rule_text_hash(rule_text)
        source = str(rule_dict.get("source") or "").strip()[:40]
        same = self._run("SELECT id, rule_id, retired_at FROM zeno_learned_rules WHERE text_hash = %s ORDER BY id", (thash,), "all") or []
        if any(r[2] is not None for r in same):
            # Retired by the owner: a PC that still holds an old copy cannot bring it back.
            return {"id": 0, "rule_id": "", "category": category, "rule_text": rule_text, "rule": rule_text, "lang": lang,
                    "author_email": author, "retired": True}
        if same:
            # Already stored: the same text is one rule, whatever id, language tag or category it arrives with.
            return {"id": int(same[0][0]), "rule_id": str(same[0][1] or ""), "category": category, "rule_text": rule_text,
                    "rule": rule_text, "lang": lang, "author_email": author, "duplicate": True}
        self._run(
            """INSERT INTO zeno_learned_rules (rule_id, category, rule_text, lang, author_email, text_hash, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (rule_id) DO UPDATE SET
                   category = EXCLUDED.category,
                   rule_text = EXCLUDED.rule_text,
                   lang = EXCLUDED.lang,
                   text_hash = EXCLUDED.text_hash,
                   author_email = CASE
                       WHEN EXCLUDED.author_email != '' THEN EXCLUDED.author_email
                       ELSE zeno_learned_rules.author_email
                   END""",
            (rule_id, category, rule_text, lang, author, thash, source),
        )
        row = self._run("SELECT id FROM zeno_learned_rules WHERE rule_id = %s", (rule_id,), "one")
        db_id = int(row[0]) if row else 0
        return {
            "id": db_id,
            "rule_id": rule_id,
            "category": category,
            "rule_text": rule_text,
            "rule": rule_text,
            "lang": lang,
            "author_email": author,
        }

    def record_learned_rule(
        self,
        key_id: Optional[int] = None,
        editor_name: str = "",
        rule: str = "",
        category: str = "general",
        language: str = "all",
        project: str = "",
        source: str = "cli_remember",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        email = self.get_key_email(key_id) if key_id is not None else ""
        res = self.add_learned_rule(
            {
                "rule_text": rule,
                "category": category,
                "lang": language,
                "author_email": email or editor_name,
                "source": source,
            },
            author_email=email or editor_name,
        )
        return int(res.get("id") or 0)

    def list_learned_rules(self) -> List[Dict[str, Any]]:
        """Every team rule, retired ones included (admins)."""
        rows = self._run(
            "SELECT id, rule_id, category, rule_text, lang, author_email, created_at, text_hash, retired_at, source "
            "FROM zeno_learned_rules ORDER BY id DESC", (), "all") or []
        cols = ("id", "rule_id", "category", "rule_text", "lang", "author_email", "created_at", "text_hash", "retired_at", "source")
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            ts = d.get("created_at")
            d["created_at"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
            d["retired"] = d.get("retired_at") is not None
            out.append(d)
        return out

    def retire_learned_rules(self, ids: List[int], retired: bool = True) -> int:
        """Hide rules from every PC (or bring them back). Nothing is deleted. Every stored copy of the same text goes with it, so a
        duplicate cannot keep the rule alive. Returns how many rows changed."""
        hashes = set()
        for i in ids:
            row = self._run("SELECT text_hash FROM zeno_learned_rules WHERE id = %s", (int(i),), "one")
            if row and row[0]:
                hashes.add(str(row[0]))
        changed = 0
        for h in hashes:
            n = self._run("SELECT COUNT(*) FROM zeno_learned_rules WHERE text_hash = %s AND retired_at IS " + ("NULL" if retired else "NOT NULL"), (h,), "one")
            changed += int(n[0]) if n else 0
            if retired:
                self._run("UPDATE zeno_learned_rules SET retired_at = %s WHERE text_hash = %s AND retired_at IS NULL", (time.time(), h))
            else:
                self._run("UPDATE zeno_learned_rules SET retired_at = NULL WHERE text_hash = %s", (h,))
        return changed

    def get_learnings(
        self,
        limit: int = 50,
        lang: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        lim = max(1, min(int(limit or 50), 500))
        norm_lang = str(lang or language or "").strip().lower()
        if norm_lang and norm_lang not in ("all", "auto", "*"):
            rows = self._run(
                """SELECT id, rule_id, category, rule_text, lang, author_email, created_at
                   FROM zeno_learned_rules
                   WHERE retired_at IS NULL AND LOWER(lang) IN ('all', %s)
                   ORDER BY id DESC LIMIT %s""",
                (norm_lang, lim),
                "all",
            )
        else:
            rows = self._run(
                """SELECT id, rule_id, category, rule_text, lang, author_email, created_at
                   FROM zeno_learned_rules
                   WHERE retired_at IS NULL
                   ORDER BY id DESC LIMIT %s""",
                (lim,),
                "all",
            )
        cols = ("id", "rule_id", "category", "rule_text", "lang", "author_email", "created_at")
        retired_hashes = sorted({str(r[0]) for r in (self._run(
            "SELECT text_hash FROM zeno_learned_rules WHERE retired_at IS NOT NULL AND text_hash IS NOT NULL", (), "all") or [])})
        retired_set = set(retired_hashes)
        learned_rules: List[Dict[str, Any]] = []
        seen_text: set = set()
        for r in rows or []:
            item = dict(zip(cols, r))
            thash = rule_text_hash(item.get("rule_text"))
            if thash in retired_set or thash in seen_text:      # retired rules are hidden; one row per text
                continue
            seen_text.add(thash)
            ts = item.get("created_at")
            item["created_at"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
            item["rule"] = item.get("rule_text", "")
            learned_rules.append(item)

        runs = self.list_runs(limit=lim)
        edl_diffs: List[Dict[str, Any]] = []
        language_patterns: Dict[str, Dict[str, Any]] = {}
        assets_used_counts: Dict[str, int] = {}

        for run in runs:
            rlang = str(run.get("language") or "auto").lower()
            if norm_lang and norm_lang not in ("all", "auto", "*") and rlang != norm_lang:
                continue
            lp = language_patterns.setdefault(
                rlang,
                {"runs": 0, "total_video_seconds": 0.0, "take_swaps": 0, "zoom_changes": 0, "cut_trims": 0},
            )
            lp["runs"] += 1
            lp["total_video_seconds"] = round(lp["total_video_seconds"] + float(run.get("video_seconds") or 0.0), 1)

            diff = run.get("edl_diff") if isinstance(run.get("edl_diff"), dict) else {}
            if diff:
                tsw = len(diff.get("take_swaps") or [])
                ctd = len(diff.get("cut_timing_deltas") or [])
                zch = len((diff.get("zoom_changes") or {}).get("changes") or []) if isinstance(diff.get("zoom_changes"), dict) else 0
                lp["take_swaps"] += tsw
                lp["cut_trims"] += ctd
                lp["zoom_changes"] += zch
                edl_diffs.append({
                    "run_id": run.get("id"),
                    "editor_name": run.get("editor_name"),
                    "user_email": run.get("user_email"),
                    "project": run.get("project"),
                    "language": rlang,
                    "created_at": run.get("created_at"),
                    "edl_diff": diff,
                })

            assets = run.get("assets_used") if isinstance(run.get("assets_used"), dict) else {}
            for k, v in assets.items():
                if v:
                    tag = f"{k}:{v}"
                    assets_used_counts[tag] = assets_used_counts.get(tag, 0) + 1

        return {
            "learned_rules": learned_rules,
            "retired_hashes": retired_hashes,
            "edl_diffs": edl_diffs,
            "language_patterns": language_patterns,
            "assets_used": assets_used_counts,
        }

    def list(self) -> List[Dict[str, Any]]:
        rows = self._run(
            "SELECT id, name, email, created_at, last_used_at, revoked_at, requests, audio_seconds, input_tokens, output_tokens, total_tokens, "
            "zeno_version, version_seen_at, client_platform FROM zeno_keys ORDER BY id DESC",
            (),
            "all",
        )
        cols = ("id", "name", "email", "created_at", "last_used_at", "revoked_at", "requests", "audio_seconds", "input_tokens", "output_tokens", "total_tokens",
                "zeno_version", "version_seen_at", "client_platform")
        return [dict(zip(cols, r)) for r in rows]

    def usage_runs(self, since: float = 0.0, limit: int = 5000, slim: bool = False) -> List[Dict[str, Any]]:
        """Only the numbers of each run (no plan files, no per-clip detail): what the Usage page needs, kept small.
        `slim` leaves out each run's job report, which is the big part and which the Usage page no longer reads."""
        rows = self._run(
            """SELECT id, editor_name, user_email, project, language, agent_name, agent_model, clips_count, video_seconds,
                      transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                      agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens, created_at, """
            + ("'{}'" if slim else "job_report_json") + """
               FROM zeno_runs WHERE created_at >= %s ORDER BY id DESC LIMIT %s""",
            (float(since), int(limit)),
            "all",
        )
        cols = ("id", "editor_name", "user_email", "project", "language", "agent_name", "agent_model", "clips_count", "video_seconds",
                "transcribe_in_tokens", "transcribe_out_tokens", "transcribe_total_tokens",
                "agent_in_tokens", "agent_cached_tokens", "agent_out_tokens", "agent_total_tokens", "grand_total_tokens", "created_at", "job_report")
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            try:
                d["job_report"] = json.loads(d.get("job_report") or "{}")
            except Exception:
                d["job_report"] = {}
            out.append(d)
        return out

    _TX_COLS = ("id", "user_name", "created_at", "status", "http_status", "model", "failed_models", "words", "audio_seconds",
                "bytes", "seconds", "in_tokens", "out_tokens", "cached", "reason", "zeno_version")

    def log_transcription(self, key_id: Optional[int], user_name: str, status: str, http_status: int = 200, model: str = "",
                          failed_models: str = "", words: int = 0, audio_seconds: float = 0.0, size: int = 0, seconds: float = 0.0,
                          in_tokens: int = 0, out_tokens: int = 0, cached: bool = False, reason: str = "", zeno_version: str = "") -> None:
        """status is ok, incomplete (transcript far shorter than the audio) or failed."""
        self._run(
            """INSERT INTO zeno_transcriptions (key_id, user_name, created_at, status, http_status, model, failed_models, words,
                   audio_seconds, bytes, seconds, in_tokens, out_tokens, cached, reason, zeno_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (key_id, user_name[:120], time.time(), status, int(http_status), model[:80], failed_models[:300], int(words),
             float(audio_seconds), int(size), float(seconds), int(in_tokens), int(out_tokens), 1 if cached else 0, reason[:300],
             (zeno_version or "")[:40]),
        )

    def transcription_events(self, since: float = 0.0, limit: int = 3000) -> List[Dict[str, Any]]:
        rows = self._run(
            "SELECT " + ", ".join(self._TX_COLS) + " FROM zeno_transcriptions WHERE created_at >= %s ORDER BY id DESC LIMIT %s",
            (float(since), int(limit)), "all")
        return [dict(zip(self._TX_COLS, r)) for r in rows]

    def gemini_usage(self, since: float = 0.0, limit: int = 200000) -> Dict[str, Any]:
        """Gemini transcription calls as numbers only: hourly buckets per editor and model (calls, failures, tokens, audio seconds)
        and each editor's busiest minute. The admin dashboard turns these into the Gemini tab (list, spend and the wave chart)."""
        rows = self._run(
            "SELECT user_name, created_at, status, model, in_tokens, out_tokens, audio_seconds, seconds FROM zeno_transcriptions "
            "WHERE created_at >= %s ORDER BY created_at LIMIT %s", (float(since), int(limit)), "all") or []
        off = 19800   # buckets follow IST hours
        buckets: Dict[Tuple[int, str, str], Dict[str, Any]] = {}
        per_min: Dict[Tuple[str, int], int] = {}
        users: Dict[str, Dict[str, Any]] = {}
        for name, at, status, model, tin, tout, aud, sec in rows:
            at = float(at or 0)
            name = str(name or "unknown")
            model = str(model or "")
            hour = int((at + off) // 3600 * 3600 - off)
            b = buckets.setdefault((hour, name, model), {"t": hour, "u": name, "m": model, "n": 0, "bad": 0, "in": 0, "out": 0, "aud": 0.0, "sec": 0.0})
            b["n"] += 1
            b["bad"] += 0 if status == "ok" else 1
            b["in"] += int(tin or 0)
            b["out"] += int(tout or 0)
            b["aud"] += float(aud or 0)
            b["sec"] += float(sec or 0)
            k = (name, int(at // 60))
            per_min[k] = per_min.get(k, 0) + 1
            u = users.setdefault(name, {"name": name, "peak_rpm": 0, "last": 0.0})
            u["last"] = max(u["last"], at)
        for (name, _m), c in per_min.items():
            users[name]["peak_rpm"] = max(users[name]["peak_rpm"], c)
        out_b = sorted(buckets.values(), key=lambda b: b["t"])
        for b in out_b:
            b["aud"], b["sec"] = round(b["aud"], 1), round(b["sec"], 1)
        return {"buckets": out_b, "users": sorted(users.values(), key=lambda u: -u["last"]), "tz_offset": off, "rows": len(rows)}

    def list_runs(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._run(
            """SELECT id, key_id, editor_name, user_email, project, language, agent_name, agent_model,
                      clips_count, video_seconds, transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                      agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens,
                      clips_json, initial_edl, final_edl, edl_diff_json, learned_rules_json, assets_used_json, created_at
               FROM zeno_runs ORDER BY id DESC LIMIT %s""",
            (int(limit),),
            "all",
        )
        cols = (
            "id", "key_id", "editor_name", "user_email", "project", "language", "agent_name", "agent_model",
            "clips_count", "video_seconds", "transcribe_in_tokens", "transcribe_out_tokens", "transcribe_total_tokens",
            "agent_in_tokens", "agent_cached_tokens", "agent_out_tokens", "agent_total_tokens", "grand_total_tokens",
            "clips_json", "initial_edl", "final_edl", "edl_diff_json", "learned_rules_json", "assets_used_json", "created_at",
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(zip(cols, r))
            for json_col, target_col, fallback in (
                ("clips_json", "clips", []),
                ("edl_diff_json", "edl_diff", {}),
                ("learned_rules_json", "learned_rules", []),
                ("assets_used_json", "assets_used", {}),
            ):
                try:
                    item[target_col] = json.loads(item.pop(json_col) or json.dumps(fallback))
                except Exception:
                    item[target_col] = fallback
            out.append(item)
        return out

    # -- transcript cache --------------------------------------------------------------------
    TRANSCRIPT_TTL_DAYS = 30

    def get_cached_transcript(self, cache_key: str) -> Optional[Dict[str, Any]]:
        row = self._run("SELECT result_json, created_at FROM zeno_transcript_cache WHERE cache_key = %s", (cache_key,), "one")
        if not row or time.time() - float(row[1]) > self.TRANSCRIPT_TTL_DAYS * 86400:
            return None
        self._run("UPDATE zeno_transcript_cache SET hits = hits + 1 WHERE cache_key = %s", (cache_key,))
        return json.loads(row[0])

    def put_cached_transcript(self, cache_key: str, result: Dict[str, Any]) -> None:
        now = time.time()
        self._run(
            "INSERT INTO zeno_transcript_cache (cache_key, result_json, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (cache_key) DO UPDATE SET result_json = excluded.result_json, created_at = excluded.created_at",
            (cache_key, json.dumps(result, ensure_ascii=False), now),
        )
        self._run("DELETE FROM zeno_transcript_cache WHERE created_at < %s", (now - self.TRANSCRIPT_TTL_DAYS * 86400,))

    # -- format observations and recipes -----------------------------------------------------
    def add_format_observation(self, key_id: Optional[int], style: str, script_hash: str, verified: bool, video_seconds: float, payload: Dict[str, Any]) -> None:
        self._run(
            "INSERT INTO zeno_format_observations (key_id, style, script_hash, verified, video_seconds, payload_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (key_id, style, script_hash, 1 if verified else 0, float(video_seconds), json.dumps(payload, ensure_ascii=False), time.time()),
        )

    def aggregate_formats(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Per (style, format): how many distinct jobs (distinct scripts, verified only) and the median and quartiles of each metric."""
        rows = self._run("SELECT style, script_hash, payload_json FROM zeno_format_observations WHERE verified = 1 ORDER BY id DESC LIMIT %s", (int(limit),), "all")
        groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for style, script_hash, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except ValueError:
                continue
            for f in payload.get("formats") or []:
                g = groups.setdefault((style, f.get("format", "?")), {"scripts": set(), "metrics": {}})
                if script_hash in g["scripts"]:
                    continue   # a re-render of the same script counts once
                g["scripts"].add(script_hash)
                for k, v in f.items():
                    if k != "format" and isinstance(v, (int, float)):
                        g["metrics"].setdefault(k, []).append(float(v))
        approved = {(r[0], r[1]): r[3] for r in self._run("SELECT style, fmt, stats_json, approved_at FROM zeno_format_recipes", (), "all")}
        out = []
        for (style, fmt), g in sorted(groups.items()):
            stats = {}
            for k, vals in g["metrics"].items():
                v = sorted(vals)
                pick = lambda q: round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 3)
                stats[k] = {"p25": pick(0.25), "median": pick(0.5), "p75": pick(0.75)}
            out.append({"style": style, "format": fmt, "n_jobs": len(g["scripts"]), "stats": stats, "approved_at": approved.get((style, fmt))})
        return out

    def approve_format(self, style: str, fmt: str, stats: Dict[str, Any], n_jobs: int, approved_by: str) -> None:
        self._run("DELETE FROM zeno_format_recipes WHERE style = %s AND fmt = %s", (style, fmt))
        self._run(
            "INSERT INTO zeno_format_recipes (style, fmt, stats_json, n_jobs, approved_by, approved_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (style, fmt, json.dumps(stats, ensure_ascii=False), int(n_jobs), approved_by[:80], time.time()),
        )

    def approved_formats(self) -> List[Dict[str, Any]]:
        rows = self._run("SELECT style, fmt, stats_json, n_jobs, approved_by, approved_at FROM zeno_format_recipes ORDER BY style, fmt", (), "all")
        return [{"style": r[0], "format": r[1], "stats": json.loads(r[2]), "n_jobs": r[3], "approved_by": r[4], "approved_at": r[5]} for r in rows]

    # -- knowledge (styles, formats, reference learnings) served to every computer without a deploy ---------------------
    KNOWLEDGE_KINDS = ("format", "reference")

    def put_knowledge(self, kind: str, name: str, payload: Dict[str, Any], match_text: str, embedding: Optional[List[float]], updated_by: str) -> float:
        now = time.time()
        self._run("DELETE FROM zeno_knowledge WHERE kind = %s AND name = %s", (kind, name))
        self._run(
            "INSERT INTO zeno_knowledge (kind, name, payload_json, match_text, embedding_json, updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (kind, name, json.dumps(payload, ensure_ascii=False), match_text, json.dumps(embedding) if embedding else None, updated_by[:80], now),
        )
        return now

    def knowledge_since(self, since: float = 0.0, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT kind, name, payload_json, updated_at FROM zeno_knowledge WHERE updated_at > %s"
        params: tuple = (float(since),)
        if kind:
            sql += " AND kind = %s"
            params += (kind,)
        rows = self._run(sql + " ORDER BY updated_at", params, "all")
        return [{"kind": r[0], "name": r[1], "payload": json.loads(r[2]), "updated_at": float(r[3])} for r in rows]

    def knowledge_for_match(self, kind: str) -> List[Dict[str, Any]]:
        rows = self._run("SELECT name, payload_json, match_text, embedding_json FROM zeno_knowledge WHERE kind = %s", (kind,), "all")
        return [{"name": r[0], "payload": json.loads(r[1]), "match_text": r[2], "embedding": json.loads(r[3]) if r[3] else None} for r in rows]

    def set_knowledge_embedding(self, kind: str, name: str, embedding: List[float]) -> None:
        self._run("UPDATE zeno_knowledge SET embedding_json = %s WHERE kind = %s AND name = %s", (json.dumps(embedding), kind, name))

    # -- cloud OTA software releases (served to clients without GitHub) ----------------------
    KEEP_RELEASE_BUNDLES = 3

    def save_release(
        self,
        version: str,
        sha: str,
        bundle_bytes: bytes,
        notes: str = "",
        mandatory: bool = False,
        dmg_url: str = "",
        exe_url: str = "",
        published_by: str = "",
    ) -> Dict[str, Any]:
        now = time.time()
        digest = hashlib.sha256(bundle_bytes).hexdigest()
        size_bytes = len(bundle_bytes)
        ver = (version or "1.0.0").strip()[:40]
        short_sha = (sha or digest[:7]).strip()[:40]
        self._run(
            "INSERT INTO zeno_releases (version, sha, bundle_sha256, size_bytes, notes, mandatory, dmg_url, exe_url, published_by, created_at, bundle_zip) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ver, short_sha, digest, size_bytes, (notes or "").strip()[:4000], 1 if mandatory else 0,
             (dmg_url or "").strip()[:500], (exe_url or "").strip()[:500], (published_by or "").strip()[:80], now, bundle_bytes),
        )
        # Keep full ZIP bytes only on the newest N releases so PostgreSQL stays tiny forever
        rows = self._run("SELECT id FROM zeno_releases WHERE bundle_zip IS NOT NULL ORDER BY id DESC", (), "all") or []
        for old_row in rows[self.KEEP_RELEASE_BUNDLES:]:
            self._run("UPDATE zeno_releases SET bundle_zip = NULL WHERE id = %s", (int(old_row[0]),))
        return {
            "version": ver,
            "sha": short_sha,
            "bundle_sha256": digest,
            "size_bytes": size_bytes,
            "notes": (notes or "").strip()[:4000],
            "mandatory": bool(mandatory),
            "dmg_url": (dmg_url or "").strip()[:500],
            "exe_url": (exe_url or "").strip()[:500],
            "published_by": (published_by or "").strip()[:80],
            "published_at": now,
        }

    def latest_release_meta(self) -> Optional[Dict[str, Any]]:
        row = self._run(
            "SELECT id, version, sha, bundle_sha256, size_bytes, notes, mandatory, dmg_url, exe_url, published_by, created_at "
            "FROM zeno_releases WHERE bundle_zip IS NOT NULL ORDER BY id DESC LIMIT 1",
            (),
            "one",
        )
        if not row:
            return None
        return {
            "id": int(row[0]),
            "version": str(row[1]),
            "sha": str(row[2]),
            "bundle_sha256": str(row[3]),
            "size_bytes": int(row[4]),
            "notes": str(row[5] or ""),
            "mandatory": bool(row[6]),
            "dmg_url": str(row[7] or ""),
            "exe_url": str(row[8] or ""),
            "published_by": str(row[9] or ""),
            "published_at": float(row[10]),
        }

    def latest_release_bundle(self, bundle_sha256: Optional[str] = None) -> Optional[Tuple[Dict[str, Any], bytes]]:
        if bundle_sha256:
            row = self._run(
                "SELECT id, version, sha, bundle_sha256, size_bytes, notes, mandatory, dmg_url, exe_url, published_by, created_at, bundle_zip "
                "FROM zeno_releases WHERE bundle_sha256 = %s AND bundle_zip IS NOT NULL ORDER BY id DESC LIMIT 1",
                (bundle_sha256.strip().lower(),),
                "one",
            )
        else:
            row = self._run(
                "SELECT id, version, sha, bundle_sha256, size_bytes, notes, mandatory, dmg_url, exe_url, published_by, created_at, bundle_zip "
                "FROM zeno_releases WHERE bundle_zip IS NOT NULL ORDER BY id DESC LIMIT 1",
                (),
                "one",
            )
        if not row or row[11] is None:
            return None
        meta = {
            "id": int(row[0]),
            "version": str(row[1]),
            "sha": str(row[2]),
            "bundle_sha256": str(row[3]),
            "size_bytes": int(row[4]),
            "notes": str(row[5] or ""),
            "mandatory": bool(row[6]),
            "dmg_url": str(row[7] or ""),
            "exe_url": str(row[8] or ""),
            "published_by": str(row[9] or ""),
            "published_at": float(row[10]),
        }
        return meta, bytes(row[11])

    def list_releases(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._run(
            "SELECT id, version, sha, bundle_sha256, size_bytes, notes, mandatory, dmg_url, exe_url, published_by, created_at, (bundle_zip IS NOT NULL) "
            "FROM zeno_releases ORDER BY id DESC LIMIT %s",
            (int(limit),),
            "all",
        ) or []
        return [
            {
                "id": int(r[0]),
                "version": str(r[1]),
                "sha": str(r[2]),
                "bundle_sha256": str(r[3]),
                "size_bytes": int(r[4]),
                "notes": str(r[5] or ""),
                "mandatory": bool(r[6]),
                "dmg_url": str(r[7] or ""),
                "exe_url": str(r[8] or ""),
                "published_by": str(r[9] or ""),
                "published_at": float(r[10]),
                "has_bundle": bool(r[11]),
            }
            for r in rows
        ]

    def revoke(self, key_id: int) -> bool:
        before = self._run("SELECT COUNT(*) FROM zeno_keys WHERE id = %s AND revoked_at IS NULL", (key_id,), "one")[0]
        if before:
            self._run("UPDATE zeno_keys SET revoked_at = %s WHERE id = %s", (time.time(), key_id))
        return bool(before)

    # -- unified FastAPI v2 tracker ---------------------------------------------------------
    @staticmethod
    def canonical_notion_name(raw: str) -> str:
        s = str(raw or "").strip()
        if not s:
            return ""
        s = re.sub(r"^(?:reel_|SQ_)", "", s, flags=re.I)
        s = re.sub(r"\.(?:mp4|mov|m4v|edl|txt|json)$", "", s, flags=re.I)
        s = re.sub(r"\s*\(\d+\)\s*$", "", s).strip()
        low = re.sub(r"\s+", " ", s).strip().lower()
        if not low or low.startswith("gdoc_"):
            return ""
        if re.match(r"^[a-z0-9_-]+_(?:clean|fixed|script|draft)$", low):
            return ""
        return low

    @staticmethod
    def extract_script_id(raw: str) -> str:
        s = str(raw or "").strip()
        if not s:
            return ""
        m = re.search(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)", s)
        if m:
            doc_id = m.group(1)
            tm = re.search(r"[?&#]tab=t\.([A-Za-z0-9_-]+)", s)
            tab_id = tm.group(1) if tm else "0"
            return f"gdoc:{doc_id}:{tab_id}"
        m2 = re.match(r"^gdoc_([A-Za-z0-9_-]+?)_t_([A-Za-z0-9_-]+)$", s)
        if m2:
            return f"gdoc:{m2.group(1)}:{m2.group(2)}"
        m3 = re.match(r"^gdoc_([A-Za-z0-9_-]+)$", s)
        if m3:
            return f"gdoc:{m3.group(1)}:0"
        base = os.path.basename(s.replace("\\", "/")).strip().lower()
        if base:
            return "file:" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
        return ""

    @staticmethod
    def classify_error(category_hint: str = "", stage: str = "", message: str = "") -> str:
        hint = str(category_hint or "").strip().lower()
        if hint in ("input_script", "gemini_transcription", "qc_plan_block", "render_system", "stuck_abandoned"):
            return hint
        st = str(stage or "").strip().lower()
        msg = str(message or "").strip().lower()
        if any(w in msg for w in ("transcrib", "gemini", "quota", "429", "503", "audio upload", "incomplete clip", "omniasr")):
            return "gemini_transcription"
        if any(w in st for w in ("transcrib",)):
            return "gemini_transcription"
        if any(w in msg for w in ("script", "gdoc", "google doc", "no video clips", "not found", "missing clip", "input validation", "no sound")):
            return "input_script"
        if st in ("inputs", "preflight"):
            return "input_script" if "ffmpeg" not in msg else "render_system"
        if any(w in msg for w in ("wrong voice", "order", "cuts fail", "verify", "qc", "plan gate", "plan_check", "missing line", "speaker", "headroom", "pause")):
            return "qc_plan_block"
        if st in ("check", "checking", "plan_gate", "qc", "verify"):
            return "qc_plan_block"
        if any(w in msg for w in ("stuck", "abandoned", "timeout")):
            return "stuck_abandoned"
        if any(w in msg for w in ("ffmpeg", "nvenc", "videotoolbox", "cuda", "disk", "memory", "render.py", "deliver")):
            return "render_system"
        if st in ("render", "deliver", "system"):
            return "render_system"
        return "render_system"

    @staticmethod
    def ist_day(ts: float, tz_offset: int = 19800) -> str:
        return datetime.fromtimestamp(float(ts), timezone(timedelta(seconds=tz_offset))).strftime("%Y-%m-%d")

    @staticmethod
    def _jload(raw: Any, default: Any) -> Any:
        if isinstance(raw, type(default)):
            return raw
        try:
            val = json.loads(raw) if raw else default
            return val if isinstance(val, type(default)) else default
        except Exception:
            return default

    def upsert_machine_heartbeat(
        self,
        editor_email: str,
        editor_name: str,
        machine_id: str,
        platform: str = "",
        zeno_version: str = "",
        verified_version: str = "",
        bundle_sha256: str = "",
        auth_source: str = "",
        pending_outbox: Optional[int] = None,
        last_update_status: str = "",
        last_update_error: str = "",
        seen_at: Optional[float] = None,
    ) -> None:
        email = (editor_email or editor_name or "unknown").strip().lower()[:120]
        mid = (machine_id or "default").strip()[:80]
        now = float(seen_at or time.time())
        row = self._run(
            "SELECT editor_name, platform, zeno_version, verified_version, bundle_sha256, auth_source, "
            "pending_outbox, last_update_status, last_update_error "
            "FROM zeno_client_machines WHERE editor_email = %s AND machine_id = %s",
            (email, mid),
            "one",
        )
        if not row:
            self._run(
                """INSERT INTO zeno_client_machines
                   (editor_email, machine_id, editor_name, platform, zeno_version, verified_version,
                    bundle_sha256, auth_source, pending_outbox, last_update_status, last_update_error, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    email, mid, (editor_name or "")[:120], (platform or "").strip()[:80],
                    (zeno_version or "").strip()[:40], (verified_version or "").strip()[:40],
                    (bundle_sha256 or "").strip()[:80], (auth_source or "").strip()[:40],
                    int(pending_outbox or 0), (last_update_status or "").strip()[:40],
                    (last_update_error or "").strip()[:500], now,
                ),
            )
            return
        self._run(
            """UPDATE zeno_client_machines SET
               editor_name = %s, platform = %s, zeno_version = %s, verified_version = %s,
               bundle_sha256 = %s, auth_source = %s, pending_outbox = %s,
               last_update_status = %s, last_update_error = %s, last_seen_at = %s
               WHERE editor_email = %s AND machine_id = %s""",
            (
                (editor_name or row[0] or "")[:120],
                (platform or "").strip()[:80] or str(row[1] or ""),
                (zeno_version or "").strip()[:40] or str(row[2] or ""),
                (verified_version or "").strip()[:40] or str(row[3] or ""),
                (bundle_sha256 or "").strip()[:80] or str(row[4] or ""),
                (auth_source or "").strip()[:40] or str(row[5] or ""),
                int(pending_outbox) if pending_outbox is not None else int(row[6] or 0),
                (last_update_status or "").strip()[:40] or str(row[7] or ""),
                (last_update_error if last_update_status else row[8] or "")[:500],
                now,
                email,
                mid,
            ),
        )

    def resolve_or_create_video_job(
        self,
        *,
        editor_email: str,
        editor_name: str,
        machine_id: str = "",
        job_key: str = "",
        notion_name: str = "",
        script_source: str = "",
        footage_path: str = "",
        project_name: str = "",
        language: str = "auto",
        zeno_version: str = "",
        client_platform: str = "",
        ts: Optional[float] = None,
    ) -> str:
        email = (editor_email or editor_name or "unknown").strip().lower()[:120]
        now = float(ts or time.time())
        jkey = str(job_key or "").strip()[:80]
        cname = self.canonical_notion_name(notion_name)
        sid = self.extract_script_id(script_source or project_name)
        cutoff = now - 14 * 86400

        matched_key = ""
        if jkey:
            hit = self._run("SELECT job_key FROM zeno_video_jobs WHERE job_key = %s", (jkey,), "one")
            if hit:
                matched_key = str(hit[0])
        if not matched_key and cname:
            hit = self._run(
                "SELECT job_key FROM zeno_video_jobs WHERE editor_email = %s AND canonical_name = %s "
                "AND last_event_at >= %s ORDER BY last_event_at DESC LIMIT 1",
                (email, cname, cutoff),
                "one",
            )
            if hit:
                matched_key = str(hit[0])
        if not matched_key and sid:
            rows = self._run(
                "SELECT job_key, canonical_name FROM zeno_video_jobs WHERE editor_email = %s AND script_id = %s "
                "AND last_event_at >= %s ORDER BY last_event_at DESC LIMIT 5",
                (email, sid, cutoff),
                "all",
            ) or []
            for r_key, r_cname in rows:
                if not cname or not str(r_cname or "").strip() or str(r_cname or "") == cname:
                    matched_key = str(r_key)
                    break

        if matched_key:
            cur = self._run(
                "SELECT editor_name, machine_id, notion_name, canonical_name, script_source, script_id, "
                "footage_path, project_name, language, zeno_version, client_platform "
                "FROM zeno_video_jobs WHERE job_key = %s",
                (matched_key,),
                "one",
            )
            if cur:
                new_notion = str(notion_name or "").strip()[:240]
                eff_notion = new_notion if (new_notion and (cname or not cur[2])) else str(cur[2] or new_notion)
                eff_cname = cname or str(cur[3] or "")
                self._run(
                    """UPDATE zeno_video_jobs SET
                       editor_name = %s, machine_id = %s, notion_name = %s, canonical_name = %s,
                       script_source = %s, script_id = %s, footage_path = %s, project_name = %s,
                       language = %s, zeno_version = %s, client_platform = %s
                       WHERE job_key = %s""",
                    (
                        (editor_name or cur[0] or "")[:120],
                        (machine_id or cur[1] or "")[:80],
                        eff_notion,
                        eff_cname[:240],
                        (script_source or cur[4] or "")[:400],
                        (sid or cur[5] or "")[:120],
                        (footage_path or cur[6] or "")[:400],
                        (project_name or cur[7] or "")[:160],
                        (language if language and language != "auto" else (cur[8] or "auto"))[:24],
                        (zeno_version or cur[9] or "")[:40],
                        (client_platform or cur[10] or "")[:80],
                        matched_key,
                    ),
                )
            return matched_key

        if not jkey:
            seed = f"{email}|{sid}|{cname}|{project_name}|{round(now, 3)}"
            jkey = "vj_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
        clean_notion = str(notion_name or "").strip()[:240]
        self._run(
            """INSERT INTO zeno_video_jobs
               (job_key, editor_email, editor_name, machine_id, notion_name, canonical_name,
                script_source, script_id, footage_path, project_name, language, zeno_version,
                client_platform, status, last_stage, opened_at, last_event_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', 'open', %s, %s)""",
            (
                jkey, email, (editor_name or "")[:120], (machine_id or "")[:80], clean_notion,
                cname[:240], (script_source or "")[:400], sid[:120], (footage_path or "")[:400],
                (project_name or "")[:160], (language or "auto")[:24], (zeno_version or "")[:40],
                (client_platform or "")[:80], now, now,
            ),
        )
        return jkey

    def _merge_prompts(self, existing_raw: str, incoming: Any, fallback_ts: float) -> List[Dict[str, Any]]:
        cur = self._jload(existing_raw, [])
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in cur:
            if not isinstance(item, dict):
                continue
            txt = " ".join(str(item.get("text") or "").split())[:2000]
            if not txt:
                continue
            at = round(float(item.get("at") or 0), 1)
            sig = (at, txt.lower())
            if sig not in seen:
                seen.add(sig)
                out.append({"at": at, "text": txt, "source": str(item.get("source") or "prompt")[:32]})
        if isinstance(incoming, str):
            incoming = [{"at": fallback_ts, "text": incoming, "source": "editor"}]
        elif isinstance(incoming, dict):
            incoming = [incoming]
        if isinstance(incoming, list):
            for item in incoming:
                if isinstance(item, str):
                    txt, at, src = " ".join(item.split())[:2000], round(float(fallback_ts), 1), "editor"
                elif isinstance(item, dict):
                    txt = " ".join(str(item.get("text") or "").split())[:2000]
                    at = round(float(item.get("at") or fallback_ts), 1)
                    src = str(item.get("source") or "prompt")[:32]
                else:
                    continue
                if not txt:
                    continue
                sig = (at, txt.lower())
                if sig not in seen:
                    seen.add(sig)
                    out.append({"at": at, "text": txt, "source": src})
        out.sort(key=lambda r: float(r.get("at") or 0))
        return out[-120:]

    def ingest_track_event(
        self,
        key_id: Optional[int],
        editor_name: str,
        editor_email: str,
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(event, dict):
            return {"event_id": "", "status": "rejected", "reason": "event must be an object"}
        event_type = str(event.get("event_type") or event.get("type") or "").strip()[:60]
        if not event_type:
            return {"event_id": str(event.get("event_id") or ""), "status": "rejected", "reason": "missing event_type"}

        now = time.time()
        try:
            client_ts = float(event.get("ts") or event.get("client_ts") or now)
            if client_ts <= 0:
                client_ts = now
        except (TypeError, ValueError):
            client_ts = now

        email = str(editor_email or event.get("editor_email") or editor_name or "unknown").strip().lower()[:120]
        ed_name = str(editor_name or event.get("editor_name") or email.split("@")[0]).strip()[:120]
        machine_id = str(event.get("machine_id") or "default").strip()[:80]
        platform = str(event.get("platform") or event.get("client_platform") or "").strip()[:80]
        ver = str(event.get("zeno_version") or "").strip()[:40]
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        merged_ev = {**payload, **event}

        event_id = str(event.get("event_id") or "").strip()[:120]
        if not event_id:
            raw_sig = json.dumps({"t": event_type, "e": email, "ts": round(client_ts, 3), "k": merged_ev.get("job_key", ""), "p": payload}, sort_keys=True, ensure_ascii=False)
            event_id = "ev_" + hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:24]

        dup = self._run("SELECT job_key FROM zeno_track_events WHERE event_id = %s", (event_id,), "one")
        if dup:
            return {"event_id": event_id, "status": "duplicate", "job_key": str(dup[0] or "")}

        if event_type in ("update_installed", "update_failed"):
            status_str = "installed" if event_type == "update_installed" else "failed"
            before_v = str(merged_ev.get("before_version") or "")[:40]
            after_v = str(merged_ev.get("after_version") or ver or "")[:40]
            sha_v = str(merged_ev.get("bundle_sha256") or "")[:80]
            files_upd = int(merged_ev.get("files_updated") or 0)
            err_msg = str(merged_ev.get("error_message") or merged_ev.get("reason") or "")[:500]
            self._run(
                """INSERT INTO zeno_update_receipts
                   (event_id, editor_email, editor_name, machine_id, before_version, after_version,
                    bundle_sha256, status, platform, files_updated, error_message, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
                (event_id, email, ed_name, machine_id, before_v, after_v, sha_v, status_str, platform, files_upd, err_msg, client_ts),
            )
            self.upsert_machine_heartbeat(
                email, ed_name, machine_id, platform=platform,
                zeno_version=after_v or ver,
                verified_version=after_v if status_str == "installed" else "",
                bundle_sha256=sha_v if status_str == "installed" else "",
                pending_outbox=merged_ev.get("pending_outbox"),
                last_update_status=status_str,
                last_update_error=err_msg,
                seen_at=client_ts,
            )
            if status_str == "installed" and after_v and key_id is not None:
                try:
                    self.note_version(key_id, after_v, platform=platform, source="verified install", editor_name=ed_name)
                except Exception:
                    pass
            self._run(
                """INSERT INTO zeno_track_events
                   (event_id, job_key, event_type, editor_email, editor_name, machine_id, stage,
                    error_category, error_message, payload_json, client_ts, created_at)
                   VALUES (%s, '', %s, %s, %s, %s, 'update', %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
                (event_id, event_type, email, ed_name, machine_id,
                 "render_system" if status_str == "failed" else "", err_msg,
                 json.dumps(payload or {}, ensure_ascii=False)[:20000], client_ts, now),
            )
            return {"event_id": event_id, "status": "accepted", "job_key": ""}

        if event_type == "heartbeat" and not (
            merged_ev.get("job_key") or merged_ev.get("notion_name") or merged_ev.get("script_source") or merged_ev.get("project_name")
        ):
            self.upsert_machine_heartbeat(
                email, ed_name, machine_id, platform=platform, zeno_version=ver,
                auth_source=str(merged_ev.get("auth_source") or ""),
                pending_outbox=merged_ev.get("pending_outbox"),
                seen_at=client_ts,
            )
            self._run(
                """INSERT INTO zeno_track_events
                   (event_id, job_key, event_type, editor_email, editor_name, machine_id, stage,
                    error_category, error_message, payload_json, client_ts, created_at)
                   VALUES (%s, '', %s, %s, %s, %s, 'heartbeat', '', '', %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
                (event_id, event_type, email, ed_name, machine_id,
                 json.dumps(payload or {}, ensure_ascii=False)[:10000], client_ts, now),
            )
            return {"event_id": event_id, "status": "accepted", "job_key": ""}

        self.upsert_machine_heartbeat(
            email, ed_name, machine_id, platform=platform, zeno_version=ver,
            auth_source=str(merged_ev.get("auth_source") or ""),
            pending_outbox=merged_ev.get("pending_outbox"),
            seen_at=client_ts,
        )

        job_key = self.resolve_or_create_video_job(
            editor_email=email,
            editor_name=ed_name,
            machine_id=machine_id,
            job_key=str(merged_ev.get("job_key") or ""),
            notion_name=str(merged_ev.get("notion_name") or merged_ev.get("video_name") or ""),
            script_source=str(merged_ev.get("script_source") or merged_ev.get("script") or ""),
            footage_path=str(merged_ev.get("footage_path") or ""),
            project_name=str(merged_ev.get("project_name") or merged_ev.get("project") or ""),
            language=str(merged_ev.get("language") or "auto"),
            zeno_version=ver,
            client_platform=platform,
            ts=client_ts,
        )

        row = self._run(
            """SELECT status, last_stage, opened_at, first_rendered_at, first_completed_at, first_completed_date,
                      last_event_at, unique_clips_count, seen_clip_hashes_json,
                      gemini_calls, gemini_retry_calls, gemini_in_tokens, gemini_out_tokens,
                      gemini_retry_in_tokens, gemini_retry_out_tokens, gemini_audio_seconds,
                      agent_name, agent_model, agent_calls, agent_in_tokens, agent_cache_write_tokens,
                      agent_cache_read_tokens, agent_out_tokens, agent_total_tokens,
                      user_prompt_count, user_prompts_json, render_count, revision_count, revisions_json,
                      output_folder, output_files_json, error_count, block_count,
                      last_error_category, last_error_message, last_error_at
               FROM zeno_video_jobs WHERE job_key = %s""",
            (job_key,),
            "one",
        )
        (
            cur_status, cur_stage, opened_at, first_rendered_at, first_completed_at, first_completed_date,
            last_event_at, uniq_clips, seen_hashes_raw,
            g_calls, g_retries, g_in, g_out, gr_in, gr_out, g_aud,
            a_name, a_model, a_calls, a_in, a_cw, a_cr, a_out, a_tot,
            p_cnt, prompts_raw, r_cnt, rev_cnt, revs_raw,
            out_folder, out_files_raw, err_cnt, blk_cnt,
            last_err_cat, last_err_msg, last_err_at,
        ) = row

        seen_hashes: List[str] = [str(x) for x in self._jload(seen_hashes_raw, []) if x]
        seen_set = set(seen_hashes)
        prompts = self._merge_prompts(prompts_raw, merged_ev.get("prompts") or merged_ev.get("prompt_text"), client_ts)
        revs = self._jload(revs_raw, [])
        out_files = self._jload(out_files_raw, [])

        stage = str(merged_ev.get("stage") or cur_stage or event_type)[:40]
        err_cat = ""
        err_msg = ""

        def _unique_clip_count(items: List[str]) -> int:
            raw_clips = {x for x in items if x and not x.startswith("tx:")}
            if raw_clips:
                return len(raw_clips)
            return len({x[3:] for x in items if x.startswith("tx:")})

        # Merge unique clip hashes when provided
        for ch in merged_ev.get("clip_hashes") or []:
            cs = str(ch or "").strip()[:64]
            if cs and cs not in seen_set:
                seen_set.add(cs)
                seen_hashes.append(cs)
        if merged_ev.get("unique_clips_count") and not seen_hashes:
            uniq_clips = max(int(uniq_clips or 0), int(merged_ev.get("unique_clips_count") or 0))
        else:
            uniq_clips = max(int(uniq_clips or 0), _unique_clip_count(seen_hashes))

        # Apply agent token & prompt updates whenever reported (taking latest monotonic snapshot)
        if merged_ev.get("agent_name"):
            a_name = str(merged_ev.get("agent_name"))[:60]
        if merged_ev.get("agent_model"):
            a_model = str(merged_ev.get("agent_model"))[:80]
        if merged_ev.get("agent_calls") is not None:
            a_calls = max(int(a_calls or 0), int(merged_ev.get("agent_calls") or 0))
        if merged_ev.get("agent_in_tokens") is not None:
            a_in = max(int(a_in or 0), int(merged_ev.get("agent_in_tokens") or 0))
        if merged_ev.get("agent_cache_write_tokens") is not None:
            a_cw = max(int(a_cw or 0), int(merged_ev.get("agent_cache_write_tokens") or 0))
        if merged_ev.get("agent_cache_read_tokens") is not None:
            a_cr = max(int(a_cr or 0), int(merged_ev.get("agent_cache_read_tokens") or 0))
        if merged_ev.get("agent_out_tokens") is not None:
            a_out = max(int(a_out or 0), int(merged_ev.get("agent_out_tokens") or 0))
        if merged_ev.get("agent_total_tokens") is not None:
            a_tot = max(int(a_tot or 0), int(merged_ev.get("agent_total_tokens") or 0))
        elif any(merged_ev.get(k) is not None for k in ("agent_in_tokens", "agent_cache_read_tokens", "agent_out_tokens")):
            a_tot = max(int(a_tot or 0), int(a_in or 0) + int(a_cr or 0) + int(a_out or 0))

        # Apply Gemini totals snapshot if present (from inspect_done / run_telemetry)
        if event_type in ("inspect_done", "run_telemetry"):
            g_in = max(int(g_in or 0), int(merged_ev.get("gemini_in_tokens") or 0))
            g_out = max(int(g_out or 0), int(merged_ev.get("gemini_out_tokens") or 0))

        if event_type in ("job_open", "inspect_start"):
            if cur_status not in ("done", "rendered"):
                cur_status = "open"
            stage = merged_ev.get("stage") or "open"
        elif event_type in ("gemini_call", "gemini_retry"):
            ch = str(merged_ev.get("clip_hash") or "").strip()[:64]
            tx_key = f"tx:{ch}" if ch else ""
            in_t = int(merged_ev.get("in_tokens") or 0)
            out_t = int(merged_ev.get("out_tokens") or 0)
            aud_s = float(merged_ev.get("audio_seconds") or 0.0)
            is_retry = bool(merged_ev.get("is_retry")) or event_type == "gemini_retry" or (bool(tx_key) and tx_key in seen_set)
            g_calls = int(g_calls or 0) + 1
            g_in = int(g_in or 0) + in_t
            g_out = int(g_out or 0) + out_t
            g_aud = float(g_aud or 0.0) + aud_s
            if is_retry:
                g_retries = int(g_retries or 0) + 1
                gr_in = int(gr_in or 0) + in_t
                gr_out = int(gr_out or 0) + out_t
            if tx_key:
                if tx_key not in seen_set:
                    seen_set.add(tx_key)
                    seen_hashes.append(tx_key)
                uniq_clips = max(uniq_clips, _unique_clip_count(seen_hashes))
            if cur_status not in ("done", "rendered"):
                cur_status = "transcribing"
            stage = "transcribe"
            call_status = str(merged_ev.get("call_status") or merged_ev.get("status") or "ok").lower()
            if call_status not in ("ok", "success", ""):
                err_cnt = int(err_cnt or 0) + 1
                err_cat = "gemini_transcription"
                err_msg = str(merged_ev.get("error_message") or merged_ev.get("reason") or call_status)[:1000]
                last_err_cat, last_err_msg, last_err_at = err_cat, err_msg, client_ts
        elif event_type == "inspect_done":
            if cur_status not in ("done", "rendered"):
                cur_status = "editing"
            stage = "planning"
            for w in merged_ev.get("warnings") or []:
                wtxt = str(w or "").strip()
                if wtxt:
                    err_cnt = int(err_cnt or 0) + 1
                    err_cat = "gemini_transcription"
                    err_msg = wtxt[:1000]
                    last_err_cat, last_err_msg, last_err_at = err_cat, err_msg, client_ts
        elif event_type in ("stage_check", "qc_passed"):
            if cur_status not in ("done", "rendered"):
                cur_status = "editing"
            if merged_ev.get("passed") is False:
                blk_cnt = int(blk_cnt or 0) + 1
                err_cat = "qc_plan_block"
                err_msg = str(merged_ev.get("error_message") or merged_ev.get("reason") or "Plan check blocked")[:1000]
                last_err_cat, last_err_msg, last_err_at = err_cat, err_msg, client_ts
        elif event_type in ("qc_failed", "job_error", "job_blocked"):
            raw_msg = str(merged_ev.get("error_message") or merged_ev.get("reason") or event_type)[:1000]
            err_cat = self.classify_error(str(merged_ev.get("error_category") or ""), stage, raw_msg)
            err_msg = raw_msg
            last_err_cat, last_err_msg, last_err_at = err_cat, err_msg, client_ts
            if event_type == "job_blocked":
                blk_cnt = int(blk_cnt or 0) + 1
                if cur_status != "done":
                    cur_status = "blocked"
            else:
                err_cnt = int(err_cnt or 0) + 1
                if cur_status != "done":
                    cur_status = "failed"
        elif event_type == "video_rendered":
            r_cnt = int(r_cnt or 0) + 1
            if first_rendered_at is None:
                first_rendered_at = client_ts
            if cur_status != "done":
                cur_status = "rendered"
            stage = "rendered"
        elif event_type in ("video_delivered", "job_done"):
            if merged_ev.get("output_folder"):
                out_folder = str(merged_ev.get("output_folder"))[:400]
            if isinstance(merged_ev.get("output_files"), list) and merged_ev.get("output_files"):
                out_files = [str(x)[:160] for x in merged_ev["output_files"][:30]]
            stage = "done"
            if first_completed_at is None:
                cur_status = "done"
                first_completed_at = client_ts
                first_completed_date = self.ist_day(client_ts)
                if r_cnt == 0:
                    r_cnt = 1
            else:
                # Deduplicate back-to-back deliver.py + zeno.py completion events for the same render pass (<= 90s)
                last_deliv_ts = float(revs[-1].get("at") if revs else first_completed_at)
                is_explicit_rev = bool(merged_ev.get("is_revision"))
                if is_explicit_rev or (client_ts - last_deliv_ts) > 90.0:
                    rev_cnt = int(rev_cnt or 0) + 1
                    r_cnt = max(int(r_cnt or 0), rev_cnt + 1)
                    revs.append({
                        "revision": rev_cnt + 1,
                        "at": round(client_ts, 1),
                        "date": self.ist_day(client_ts),
                        "reason": str(merged_ev.get("revision_reason") or merged_ev.get("reason") or "post-render revision")[:300],
                        "agent_total_tokens": int(a_tot or 0),
                        "prompts": len(prompts),
                        "output_folder": out_folder,
                    })
                    revs = revs[-30:]
                cur_status = "done"

        eff_opened = min(float(opened_at), client_ts)
        eff_last = max(float(last_event_at), client_ts)
        end_ref = float(first_completed_at) if first_completed_at is not None else eff_last
        total_dur = round(max(0.0, end_ref - eff_opened), 2)
        p_cnt = max(int(p_cnt or 0), len(prompts))

        self._run(
            """UPDATE zeno_video_jobs SET
               status = %s, last_stage = %s, opened_at = %s, first_rendered_at = %s,
               first_completed_at = %s, first_completed_date = %s, last_event_at = %s,
               total_duration_sec = %s, unique_clips_count = %s, seen_clip_hashes_json = %s,
               gemini_calls = %s, gemini_retry_calls = %s, gemini_in_tokens = %s, gemini_out_tokens = %s,
               gemini_retry_in_tokens = %s, gemini_retry_out_tokens = %s, gemini_audio_seconds = %s,
               agent_name = %s, agent_model = %s, agent_calls = %s, agent_in_tokens = %s,
               agent_cache_write_tokens = %s, agent_cache_read_tokens = %s, agent_out_tokens = %s,
               agent_total_tokens = %s, user_prompt_count = %s, user_prompts_json = %s,
               render_count = %s, revision_count = %s, revisions_json = %s,
               output_folder = %s, output_files_json = %s,
               error_count = %s, block_count = %s, last_error_category = %s,
               last_error_message = %s, last_error_at = %s
               WHERE job_key = %s""",
            (
                cur_status, stage, eff_opened, first_rendered_at,
                first_completed_at, first_completed_date, eff_last,
                total_dur, uniq_clips, json.dumps(seen_hashes[:200], ensure_ascii=False),
                int(g_calls or 0), int(g_retries or 0), int(g_in or 0), int(g_out or 0),
                int(gr_in or 0), int(gr_out or 0), round(float(g_aud or 0.0), 2),
                a_name, a_model, int(a_calls or 0), int(a_in or 0),
                int(a_cw or 0), int(a_cr or 0), int(a_out or 0),
                int(a_tot or 0), p_cnt, json.dumps(prompts, ensure_ascii=False),
                int(r_cnt or 0), int(rev_cnt or 0), json.dumps(revs, ensure_ascii=False),
                out_folder, json.dumps(out_files, ensure_ascii=False),
                int(err_cnt or 0), int(blk_cnt or 0), str(last_err_cat or ""),
                str(last_err_msg or "")[:1000], last_err_at,
                job_key,
            ),
        )
        self._run(
            """INSERT INTO zeno_track_events
               (event_id, job_key, event_type, editor_email, editor_name, machine_id, stage,
                error_category, error_message, payload_json, client_ts, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (event_id) DO NOTHING""",
            (
                event_id, job_key, event_type, email, ed_name, machine_id, stage,
                err_cat, err_msg[:1000], json.dumps(payload or {}, ensure_ascii=False)[:30000],
                client_ts, now,
            ),
        )
        return {"event_id": event_id, "status": "accepted", "job_key": job_key}

    def record_transcribe_for_job(
        self,
        key_id: Optional[int],
        editor_name: str,
        editor_email: str,
        job_key: str,
        clip_hash: str,
        clip_name: str = "",
        is_retry: bool = False,
        status: str = "ok",
        in_tokens: int = 0,
        out_tokens: int = 0,
        audio_seconds: float = 0.0,
        reason: str = "",
        version: str = "",
        platform: str = "",
        machine_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not job_key and not clip_hash:
            return None
        ev_id = "tr_" + hashlib.sha256(
            f"{job_key}|{clip_hash}|{time.time()}|{secrets.token_hex(4)}".encode("utf-8")
        ).hexdigest()[:24]
        return self.ingest_track_event(
            key_id,
            editor_name,
            editor_email,
            {
                "event_id": ev_id,
                "event_type": "gemini_retry" if is_retry else "gemini_call",
                "job_key": job_key,
                "machine_id": machine_id or "default",
                "zeno_version": version,
                "platform": platform,
                "ts": time.time(),
                "payload": {
                    "clip_hash": clip_hash,
                    "clip_name": clip_name,
                    "is_retry": bool(is_retry),
                    "call_status": status,
                    "in_tokens": int(in_tokens or 0),
                    "out_tokens": int(out_tokens or 0),
                    "audio_seconds": float(audio_seconds or 0.0),
                    "reason": str(reason or "")[:400],
                },
            },
        )

    def _window_bounds(self, date_str: str = "", days: int = 1, tz_offset: int = 19800) -> Tuple[str, str, float, float]:
        tz = timezone(timedelta(seconds=tz_offset))
        now_dt = datetime.now(tz)
        if date_str:
            try:
                end_dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=tz)
            except Exception:
                end_dt = now_dt
        else:
            end_dt = now_dt
        end_start = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        span = max(1, min(365, int(days or 1)))
        start_dt = end_start - timedelta(days=span - 1)
        end_next = end_start + timedelta(days=1)
        return start_dt.strftime("%Y-%m-%d"), end_start.strftime("%Y-%m-%d"), start_dt.timestamp(), end_next.timestamp()

    def track_summary(
        self,
        date_str: str = "",
        days: int = 1,
        editor: str = "",
        tz_offset: int = 19800,
        stuck_after_sec: int = 1800,
    ) -> Dict[str, Any]:
        start_day, end_day, lo, hi = self._window_bounds(date_str, days, tz_offset)
        now = time.time()
        ed_filter = (editor or "").strip().lower()
        rows = self._run(
            """SELECT job_key, editor_email, editor_name, machine_id, notion_name, canonical_name,
                      script_source, script_id, project_name, language, zeno_version, client_platform,
                      status, last_stage, opened_at, first_rendered_at, first_completed_at, first_completed_date,
                      last_event_at, total_duration_sec, unique_clips_count, gemini_calls, gemini_retry_calls,
                      gemini_in_tokens, gemini_out_tokens, gemini_retry_in_tokens, gemini_retry_out_tokens,
                      gemini_audio_seconds, agent_name, agent_model, agent_calls, agent_in_tokens,
                      agent_cache_write_tokens, agent_cache_read_tokens, agent_out_tokens, agent_total_tokens,
                      user_prompt_count, render_count, revision_count, revisions_json,
                      output_folder, error_count, block_count, last_error_category, last_error_message, last_error_at
               FROM zeno_video_jobs
               WHERE (first_completed_date >= %s AND first_completed_date <= %s)
                  OR (opened_at >= %s AND opened_at < %s)
                  OR (last_event_at >= %s AND last_event_at < %s)
               ORDER BY last_event_at DESC LIMIT 2000""",
            (start_day, end_day, lo, hi, lo, hi),
            "all",
        ) or []

        jobs_out: List[Dict[str, Any]] = []
        by_editor: Dict[str, Dict[str, Any]] = {}
        err_cats: Dict[str, int] = {
            "input_script": 0,
            "gemini_transcription": 0,
            "qc_plan_block": 0,
            "render_system": 0,
            "stuck_abandoned": 0,
        }

        for r in rows:
            email = str(r[1] or "").lower()
            name = str(r[2] or email.split("@")[0])
            if ed_filter and ed_filter not in email and ed_filter not in name.lower():
                continue
            status = str(r[12] or "open")
            last_ev = float(r[18] or 0.0)
            is_stuck = status in ("open", "transcribing", "editing", "rendered") and (now - last_ev) >= float(stuck_after_sec)
            eff_status = "stuck" if is_stuck else status
            revs = self._jload(r[39], [])
            done_in_window = bool(r[17] and start_day <= str(r[17]) <= end_day)
            rev_in_window = sum(1 for rv in revs if start_day <= str(rv.get("date") or "") <= end_day)
            job_obj = {
                "job_key": r[0],
                "editor_email": email,
                "editor_name": name,
                "machine_id": r[3],
                "notion_name": r[4] or r[8] or r[0],
                "canonical_name": r[5],
                "script_source": r[6],
                "script_id": r[7],
                "project_name": r[8],
                "language": r[9],
                "zeno_version": r[10],
                "client_platform": r[11],
                "status": eff_status,
                "raw_status": status,
                "is_stuck": is_stuck,
                "last_stage": r[13],
                "opened_at": float(r[14] or 0.0),
                "first_rendered_at": float(r[15]) if r[15] is not None else None,
                "first_completed_at": float(r[16]) if r[16] is not None else None,
                "first_completed_date": r[17] or "",
                "done_in_window": done_in_window,
                "revisions_in_window": rev_in_window,
                "last_event_at": last_ev,
                "total_duration_sec": float(r[19] or 0.0),
                "unique_clips_count": int(r[20] or 0),
                "gemini_calls": int(r[21] or 0),
                "gemini_retry_calls": int(r[22] or 0),
                "gemini_in_tokens": int(r[23] or 0),
                "gemini_out_tokens": int(r[24] or 0),
                "gemini_retry_in_tokens": int(r[25] or 0),
                "gemini_retry_out_tokens": int(r[26] or 0),
                "gemini_audio_seconds": float(r[27] or 0.0),
                "agent_name": r[28] or "",
                "agent_model": r[29] or "",
                "agent_calls": int(r[30] or 0),
                "agent_in_tokens": int(r[31] or 0),
                "agent_cache_write_tokens": int(r[32] or 0),
                "agent_cache_read_tokens": int(r[33] or 0),
                "agent_out_tokens": int(r[34] or 0),
                "agent_total_tokens": int(r[35] or 0),
                "user_prompt_count": int(r[36] or 0),
                "render_count": int(r[37] or 0),
                "revision_count": int(r[38] or 0),
                "revisions": revs,
                "output_folder": r[40] or "",
                "error_count": int(r[41] or 0),
                "block_count": int(r[42] or 0),
                "last_error_category": "stuck_abandoned" if is_stuck and not r[43] else (r[43] or ""),
                "last_error_message": (f"No progress for {int((now - last_ev) // 60)}m at stage '{r[13]}'" if is_stuck and not r[44] else (r[44] or "")),
                "last_error_at": float(r[45]) if r[45] is not None else (last_ev if is_stuck else None),
            }
            jobs_out.append(job_obj)

            ed_rec = by_editor.setdefault(email, {
                "editor_email": email,
                "editor_name": name,
                "opened": 0,
                "done": 0,
                "first_pass_done": 0,
                "revisions": 0,
                "stuck": 0,
                "failed_blocked": 0,
                "unique_clips": 0,
                "gemini_calls": 0,
                "gemini_retries": 0,
                "prompts": 0,
                "agent_total_tokens": 0,
                "gemini_total_tokens": 0,
            })
            ed_rec["opened"] += 1
            if done_in_window:
                ed_rec["done"] += 1
                if int(r[38] or 0) == 0:
                    ed_rec["first_pass_done"] += 1
            ed_rec["revisions"] += rev_in_window
            if is_stuck:
                ed_rec["stuck"] += 1
                err_cats["stuck_abandoned"] += 1
            if eff_status in ("failed", "blocked"):
                ed_rec["failed_blocked"] += 1
            ed_rec["unique_clips"] += job_obj["unique_clips_count"]
            ed_rec["gemini_calls"] += job_obj["gemini_calls"]
            ed_rec["gemini_retries"] += job_obj["gemini_retry_calls"]
            ed_rec["prompts"] += job_obj["user_prompt_count"]
            ed_rec["agent_total_tokens"] += job_obj["agent_total_tokens"]
            ed_rec["gemini_total_tokens"] += job_obj["gemini_in_tokens"] + job_obj["gemini_out_tokens"]

        err_rows = self._run(
            """SELECT category_val, COUNT(*) FROM (
                   SELECT error_category AS category_val FROM zeno_track_events
                   WHERE error_category <> '' AND client_ts >= %s AND client_ts < %s
               ) sub GROUP BY category_val""",
            (lo, hi),
            "all",
        ) or []
        for cat, cnt in err_rows:
            c_key = str(cat or "")
            if c_key in err_cats:
                err_cats[c_key] += int(cnt or 0)

        fleet = self.track_fleet(tz_offset=tz_offset)
        machines_by_email = {m["editor_email"]: m for m in fleet.get("editors", [])}
        for ed_email, ed_rec in by_editor.items():
            m_info = machines_by_email.get(ed_email) or {}
            ed_rec["verified_version"] = m_info.get("verified_version") or ""
            ed_rec["zeno_version"] = m_info.get("zeno_version") or ""
            ed_rec["platform"] = m_info.get("platform") or ""

        totals = {
            "opened": len(jobs_out),
            "done": sum(1 for j in jobs_out if j["done_in_window"]),
            "first_pass_done": sum(1 for j in jobs_out if j["done_in_window"] and j["revision_count"] == 0),
            "revisions": sum(j["revisions_in_window"] for j in jobs_out),
            "in_progress": sum(1 for j in jobs_out if j["status"] in ("open", "transcribing", "editing", "rendered")),
            "stuck": sum(1 for j in jobs_out if j["is_stuck"]),
            "failed_blocked": sum(1 for j in jobs_out if j["status"] in ("failed", "blocked")),
            "unique_clips": sum(j["unique_clips_count"] for j in jobs_out),
            "gemini_calls": sum(j["gemini_calls"] for j in jobs_out),
            "gemini_retries": sum(j["gemini_retry_calls"] for j in jobs_out),
            "prompts": sum(j["user_prompt_count"] for j in jobs_out),
            "errors": sum(err_cats.values()),
            "verified_updated": fleet.get("verified_up_to_date", 0),
            "latest_version": fleet.get("latest_version", ""),
        }
        return {
            "ok": True,
            "start_date": start_day,
            "end_date": end_day,
            "now": now,
            "totals": totals,
            "error_categories": err_cats,
            "editors": sorted(by_editor.values(), key=lambda x: (-x["done"], -x["opened"], x["editor_name"])),
            "jobs": jobs_out,
            "fleet": fleet,
        }

    def track_job_detail(self, job_key: str) -> Optional[Dict[str, Any]]:
        row = self._run(
            """SELECT job_key, editor_email, editor_name, machine_id, notion_name, canonical_name,
                      script_source, script_id, footage_path, project_name, language, zeno_version, client_platform,
                      status, last_stage, opened_at, first_rendered_at, first_completed_at, first_completed_date,
                      last_event_at, total_duration_sec, unique_clips_count, seen_clip_hashes_json,
                      gemini_calls, gemini_retry_calls, gemini_in_tokens, gemini_out_tokens,
                      gemini_retry_in_tokens, gemini_retry_out_tokens, gemini_audio_seconds,
                      agent_name, agent_model, agent_calls, agent_in_tokens, agent_cache_write_tokens,
                      agent_cache_read_tokens, agent_out_tokens, agent_total_tokens,
                      user_prompt_count, user_prompts_json, render_count, revision_count, revisions_json,
                      output_folder, output_files_json, error_count, block_count,
                      last_error_category, last_error_message, last_error_at
               FROM zeno_video_jobs WHERE job_key = %s""",
            (str(job_key or "").strip(),),
            "one",
        )
        if not row:
            return None
        evs = self._run(
            """SELECT event_id, event_type, stage, error_category, error_message, payload_json, client_ts, created_at
               FROM zeno_track_events WHERE job_key = %s ORDER BY client_ts ASC, id ASC LIMIT 500""",
            (str(job_key or "").strip(),),
            "all",
        ) or []
        return {
            "job_key": row[0],
            "editor_email": row[1],
            "editor_name": row[2],
            "machine_id": row[3],
            "notion_name": row[4] or row[9] or row[0],
            "canonical_name": row[5],
            "script_source": row[6],
            "script_id": row[7],
            "footage_path": row[8],
            "project_name": row[9],
            "language": row[10],
            "zeno_version": row[11],
            "client_platform": row[12],
            "status": row[13],
            "last_stage": row[14],
            "opened_at": float(row[15] or 0.0),
            "first_rendered_at": float(row[16]) if row[16] is not None else None,
            "first_completed_at": float(row[17]) if row[17] is not None else None,
            "first_completed_date": row[18] or "",
            "last_event_at": float(row[19] or 0.0),
            "total_duration_sec": float(row[20] or 0.0),
            "unique_clips_count": int(row[21] or 0),
            "seen_clip_hashes": sorted({(x[3:] if str(x).startswith("tx:") else str(x)) for x in self._jload(row[22], []) if x}),
            "gemini_calls": int(row[23] or 0),
            "gemini_retry_calls": int(row[24] or 0),
            "gemini_in_tokens": int(row[25] or 0),
            "gemini_out_tokens": int(row[26] or 0),
            "gemini_retry_in_tokens": int(row[27] or 0),
            "gemini_retry_out_tokens": int(row[28] or 0),
            "gemini_audio_seconds": float(row[29] or 0.0),
            "agent_name": row[30] or "",
            "agent_model": row[31] or "",
            "agent_calls": int(row[32] or 0),
            "agent_in_tokens": int(row[33] or 0),
            "agent_cache_write_tokens": int(row[34] or 0),
            "agent_cache_read_tokens": int(row[35] or 0),
            "agent_out_tokens": int(row[36] or 0),
            "agent_total_tokens": int(row[37] or 0),
            "user_prompt_count": int(row[38] or 0),
            "user_prompts": self._jload(row[39], []),
            "render_count": int(row[40] or 0),
            "revision_count": int(row[41] or 0),
            "revisions": self._jload(row[42], []),
            "output_folder": row[43] or "",
            "output_files": self._jload(row[44], []),
            "error_count": int(row[45] or 0),
            "block_count": int(row[46] or 0),
            "last_error_category": row[47] or "",
            "last_error_message": row[48] or "",
            "last_error_at": float(row[49]) if row[49] is not None else None,
            "events": [
                {
                    "event_id": e[0],
                    "event_type": e[1],
                    "stage": e[2],
                    "error_category": e[3],
                    "error_message": e[4],
                    "payload": self._jload(e[5], {}),
                    "client_ts": float(e[6] or 0.0),
                    "created_at": float(e[7] or 0.0),
                }
                for e in evs
            ],
        }

    def track_errors(
        self,
        date_str: str = "",
        days: int = 1,
        category: str = "",
        editor: str = "",
        tz_offset: int = 19800,
        stuck_after_sec: int = 1800,
    ) -> Dict[str, Any]:
        start_day, end_day, lo, hi = self._window_bounds(date_str, days, tz_offset)
        now = time.time()
        cat_filter = (category or "").strip().lower()
        ed_filter = (editor or "").strip().lower()

        ev_rows = self._run(
            """SELECT e.event_id, e.job_key, e.event_type, e.editor_email, e.editor_name,
                      e.stage, e.error_category, e.error_message, e.client_ts, j.notion_name
               FROM zeno_track_events e
               LEFT JOIN zeno_video_jobs j ON j.job_key = e.job_key
               WHERE e.error_category <> '' AND e.client_ts >= %s AND e.client_ts < %s
               ORDER BY e.client_ts DESC LIMIT 500""",
            (lo, hi),
            "all",
        ) or []
        items: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {
            "input_script": 0,
            "gemini_transcription": 0,
            "qc_plan_block": 0,
            "render_system": 0,
            "stuck_abandoned": 0,
        }
        for eid, jkey, etype, email, ed_name, stage, ecat, emsg, cts, notion_name in ev_rows:
            if ed_filter and ed_filter not in str(email or "").lower() and ed_filter not in str(ed_name or "").lower():
                continue
            if cat_filter and cat_filter != str(ecat or "").lower():
                continue
            if ecat in counts:
                counts[ecat] += 1
            items.append({
                "kind": etype,
                "event_id": eid,
                "job_key": jkey or "",
                "notion_name": notion_name or jkey or "",
                "editor_email": email,
                "editor_name": ed_name,
                "stage": stage,
                "category": ecat,
                "message": emsg,
                "ts": float(cts or 0.0),
            })

        stuck_rows = self._run(
            """SELECT job_key, editor_email, editor_name, notion_name, last_stage, last_event_at, last_error_message
               FROM zeno_video_jobs
               WHERE status IN ('open', 'transcribing', 'editing', 'rendered')
                 AND last_event_at >= %s AND last_event_at < %s AND (%s - last_event_at) >= %s
               ORDER BY last_event_at DESC LIMIT 200""",
            (lo, hi, now, float(stuck_after_sec)),
            "all",
        ) or []
        for jkey, email, ed_name, notion_name, lstage, lev, lemsg in stuck_rows:
            if ed_filter and ed_filter not in str(email or "").lower() and ed_filter not in str(ed_name or "").lower():
                continue
            if cat_filter and cat_filter != "stuck_abandoned":
                continue
            counts["stuck_abandoned"] += 1
            mins = int(max(0, (now - float(lev or now)) // 60))
            items.append({
                "kind": "stuck",
                "event_id": "",
                "job_key": jkey,
                "notion_name": notion_name or jkey,
                "editor_email": email,
                "editor_name": ed_name,
                "stage": lstage,
                "category": "stuck_abandoned",
                "message": f"Stuck for {mins}m at stage '{lstage}'" + (f" ({lemsg})" if lemsg else ""),
                "ts": float(lev or 0.0),
            })

        items.sort(key=lambda x: -float(x.get("ts") or 0.0))
        return {
            "ok": True,
            "start_date": start_day,
            "end_date": end_day,
            "counts": counts,
            "items": items,
        }

    def track_fleet(self, tz_offset: int = 19800) -> Dict[str, Any]:
        latest_meta = self.latest_release_meta() or {}
        latest_ver = str(latest_meta.get("version") or "")
        latest_sha = str(latest_meta.get("bundle_sha256") or "")

        def _vtuple(v: str) -> Tuple[int, int, int]:
            nums = [int(x) for x in re.findall(r"\d+", v or "")[:3]]
            while len(nums) < 3:
                nums.append(0)
            return (nums[0], nums[1], nums[2])

        machines = self._run(
            """SELECT editor_email, machine_id, editor_name, platform, zeno_version, verified_version,
                      bundle_sha256, auth_source, pending_outbox, last_update_status, last_update_error, last_seen_at
               FROM zeno_client_machines ORDER BY last_seen_at DESC""",
            (),
            "all",
        ) or []
        by_ed: Dict[str, Dict[str, Any]] = {}
        for r in machines:
            em = str(r[0] or "").lower()
            cur = by_ed.setdefault(em, {
                "editor_email": em,
                "editor_name": str(r[2] or em.split("@")[0]),
                "platform": str(r[3] or ""),
                "zeno_version": str(r[4] or ""),
                "verified_version": str(r[5] or ""),
                "bundle_sha256": str(r[6] or ""),
                "pending_outbox": 0,
                "last_update_status": str(r[9] or ""),
                "last_update_error": str(r[10] or ""),
                "last_seen_at": float(r[11] or 0.0),
                "machines": [],
            })
            cur["machines"].append({
                "machine_id": r[1],
                "platform": r[3] or "",
                "zeno_version": r[4] or "",
                "verified_version": r[5] or "",
                "bundle_sha256": r[6] or "",
                "auth_source": r[7] or "",
                "pending_outbox": int(r[8] or 0),
                "last_update_status": r[9] or "",
                "last_update_error": r[10] or "",
                "last_seen_at": float(r[11] or 0.0),
            })
            cur["pending_outbox"] += int(r[8] or 0)
            if float(r[11] or 0.0) >= cur["last_seen_at"]:
                cur["last_seen_at"] = float(r[11] or 0.0)
                if r[3]:
                    cur["platform"] = str(r[3])
                if r[9]:
                    cur["last_update_status"] = str(r[9])
                    cur["last_update_error"] = str(r[10] or "")
            if _vtuple(str(r[4] or "")) > _vtuple(cur["zeno_version"]):
                cur["zeno_version"] = str(r[4] or "")
            if _vtuple(str(r[5] or "")) > _vtuple(cur["verified_version"]):
                cur["verified_version"] = str(r[5] or "")
                cur["bundle_sha256"] = str(r[6] or "")

        # Merge legacy key metadata so editors who haven't updated yet still show up once by email
        for k in self.list():
            if k.get("revoked_at"):
                continue
            em = str(k.get("email") or "").strip().lower()
            if not em:
                continue
            cur = by_ed.setdefault(em, {
                "editor_email": em,
                "editor_name": str(k.get("name") or em.split("@")[0]),
                "platform": str(k.get("client_platform") or ""),
                "zeno_version": str(k.get("zeno_version") or ""),
                "verified_version": "",
                "bundle_sha256": "",
                "pending_outbox": 0,
                "last_update_status": "",
                "last_update_error": "",
                "last_seen_at": float(k.get("last_used_at") or k.get("version_seen_at") or 0.0),
                "machines": [],
            })
            if not cur["platform"] and k.get("client_platform"):
                cur["platform"] = str(k.get("client_platform"))
            if _vtuple(str(k.get("zeno_version") or "")) > _vtuple(cur["zeno_version"]):
                cur["zeno_version"] = str(k.get("zeno_version") or "")
            cur["last_seen_at"] = max(cur["last_seen_at"], float(k.get("last_used_at") or 0.0))

        receipts = self._run(
            """SELECT event_id, editor_email, editor_name, machine_id, before_version, after_version,
                      bundle_sha256, status, platform, files_updated, error_message, created_at
               FROM zeno_update_receipts ORDER BY created_at DESC LIMIT 100""",
            (),
            "all",
        ) or []
        editors_list = sorted(by_ed.values(), key=lambda x: -float(x.get("last_seen_at") or 0.0))
        verified_up = sum(
            1 for e in editors_list
            if latest_ver and e.get("verified_version") and _vtuple(e["verified_version"]) >= _vtuple(latest_ver)
        )
        return {
            "ok": True,
            "latest_version": latest_ver,
            "latest_sha256": latest_sha,
            "verified_up_to_date": verified_up,
            "editors": editors_list,
            "receipts": [
                {
                    "event_id": r[0],
                    "editor_email": r[1],
                    "editor_name": r[2],
                    "machine_id": r[3],
                    "before_version": r[4],
                    "after_version": r[5],
                    "bundle_sha256": r[6],
                    "status": r[7],
                    "platform": r[8],
                    "files_updated": int(r[9] or 0),
                    "error_message": r[10],
                    "created_at": float(r[11] or 0.0),
                }
                for r in receipts
            ],
        }
