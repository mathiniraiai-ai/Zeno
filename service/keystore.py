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
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_ACTIVE_KEYS_PER_EMAIL = int(os.getenv("ZENO_MAX_KEYS_PER_EMAIL", "3"))
GOOGLE_MAX_ACTIVE_KEYS_PER_EMAIL = int(os.getenv("ZENO_GOOGLE_MAX_KEYS_PER_EMAIL", "50"))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


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

        # Transcripts by audio hash (a retry or a second editor on the same clip costs nothing), and per-job format observations
        self._run(f"""CREATE TABLE IF NOT EXISTS zeno_transcript_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            created_at {real} NOT NULL)""")
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
        t_tot = int(payload.get("transcribe_total_tokens") or (t_in + t_out))
        a_in = int(payload.get("agent_in_tokens") or 0)
        a_cache = int(payload.get("agent_cached_tokens") or 0)
        a_out = int(payload.get("agent_out_tokens") or 0)
        a_tot = int(payload.get("agent_total_tokens") or (a_in + a_cache + a_out))
        grand = int(payload.get("grand_total_tokens") or (t_tot + a_tot))

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

        now = time.time()
        self._run(
            """INSERT INTO zeno_runs (
                key_id, editor_name, user_email, project, language, agent_name, agent_model,
                clips_count, video_seconds, transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens, grand_total_tokens,
                clips_json, initial_edl, final_edl, edl_diff_json, learned_rules_json, assets_used_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                key_id,
                str(resolved_name or "unknown")[:60],
                user_email[:120],
                str(payload.get("project") or "unnamed")[:120],
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

        self._run(
            """INSERT INTO zeno_learned_rules (rule_id, category, rule_text, lang, author_email)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (rule_id) DO UPDATE SET
                   category = EXCLUDED.category,
                   rule_text = EXCLUDED.rule_text,
                   lang = EXCLUDED.lang,
                   author_email = CASE
                       WHEN EXCLUDED.author_email != '' THEN EXCLUDED.author_email
                       ELSE zeno_learned_rules.author_email
                   END""",
            (rule_id, category, rule_text, lang, author),
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
            },
            author_email=email or editor_name,
        )
        return int(res.get("id") or 0)

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
                   WHERE LOWER(lang) IN ('all', %s)
                   ORDER BY id DESC LIMIT %s""",
                (norm_lang, lim),
                "all",
            )
        else:
            rows = self._run(
                """SELECT id, rule_id, category, rule_text, lang, author_email, created_at
                   FROM zeno_learned_rules
                   ORDER BY id DESC LIMIT %s""",
                (lim,),
                "all",
            )
        cols = ("id", "rule_id", "category", "rule_text", "lang", "author_email", "created_at")
        learned_rules: List[Dict[str, Any]] = []
        for r in rows or []:
            item = dict(zip(cols, r))
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
            "edl_diffs": edl_diffs,
            "language_patterns": language_patterns,
            "assets_used": assets_used_counts,
        }

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

    def revoke(self, key_id: int) -> bool:
        before = self._run("SELECT COUNT(*) FROM zeno_keys WHERE id = %s AND revoked_at IS NULL", (key_id,), "one")[0]
        if before:
            self._run("UPDATE zeno_keys SET revoked_at = %s WHERE id = %s", (time.time(), key_id))
        return bool(before)
