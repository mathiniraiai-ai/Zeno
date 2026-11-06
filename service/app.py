"""Zeno transcription service.

One job: turn audio into a verbatim transcript with Gemini, so the Gemini key lives only here.
Team PCs send audio with a personal key; they never see the Gemini key.

Run locally:   uvicorn app:app --port 8000   (from the service/ folder)
Environment:
    GEMINI_API_KEY (and optional GEMINI_API_KEY_1..4)   the Gemini key(s), kept secret
    DATABASE_URL         Postgres address (Railway: reference the Postgres service). Without it a local SQLite file is used.
    ZENO_GOOGLE_CLIENT_ID  OAuth Web client id; Google sign-in (ID token for this client only) is how people get a key
    ZENO_SIGNUP_CODE     team code for the old name-only signup; when unset that signup is refused (403)
    ZENO_ALLOWED_EMAIL_DOMAINS  comma-separated email domains allowed to sign in (default gosupernova.live)
    ZENO_ADMIN_EMAILS    emails that may open /admin with their key; ZENO_ADMIN_ALLOW_DOMAIN=1 makes every company key admin (default off)
    ZENO_MAX_SIGNUPS_PER_HOUR  cap on keys created per hour across everyone (default 30)
    ZENO_ADMIN_PASSWORD  password for /admin (list, usage, revoke); /admin is off when unset
    ZENO_API_TOKENS      optional extra keys "name:sha256hex,name2:sha256hex" (make them with make_token.py)
    ZENO_RATE_PER_MIN    requests per person per minute (default 20)
    ZENO_MAX_UPLOAD_MB   largest audio upload (default 25)
    GEMINI_RPM           Gemini calls per minute across everyone (default 30)
    GEMINI_MODELS        model order, comma-separated (default: gemini-3.5-flash-lite,gemini-2.5-flash,gemini-flash-lite-latest)
Audio is processed in memory and never written to disk.
"""
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import random
import re
import threading
import time
import wave
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from keystore import KeyStore
from prompt import build_transcribe_prompt
from ui import ADMIN_PAGE, SIGNUP_PAGE
from usage_page import USAGE_PAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("zeno.service")
# httpx logs every request URL at INFO, and the Google sign-in check puts the user's ID token in its URL: keep tokens out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Lite first: on a 93 s clip gemini-2.5-flash timed out every time while lite answered in about 4 s with a complete transcript.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-2.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
def _prompt_version() -> str:
    """Changes whenever the transcription prompt or the model order does, so a cached transcript is never reused after either."""
    h = hashlib.sha1("gemini-3.5-flash-lite,gemini-2.5-flash,gemini-flash-lite-latest".encode())
    try:
        for line in (Path(__file__).parent / "prompt.py").read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                h.update(t.encode("utf-8"))
    except OSError:
        pass
    return h.hexdigest()[:10]


PROMPT_VERSION = _prompt_version()
app = FastAPI(title="Zeno transcription service", docs_url=None, redoc_url=None, openapi_url=None)


def _tokens() -> Dict[str, str]:
    """hash -> person name, read from the environment on each request so a change takes effect on restart."""
    out: Dict[str, str] = {}
    for item in os.getenv("ZENO_API_TOKENS", "").split(","):
        if ":" in item:
            name, digest = item.strip().split(":", 1)
            if name.strip() and digest.strip():
                out[digest.strip().lower()] = name.strip()
    return out


def _gemini_keys() -> List[str]:
    keys = [os.getenv("GEMINI_API_KEY", "").strip()] + [os.getenv(f"GEMINI_API_KEY_{i}", "").strip() for i in range(1, 5)]
    return [k for k in keys if k]


_store: Optional[KeyStore] = None
_store_lock = threading.Lock()


def store() -> KeyStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = KeyStore(os.getenv("DATABASE_URL"))
            log.info("key store ready: %s", _store.backend.split(":")[0])
        return _store


def authenticate(authorization: Optional[str]) -> Tuple[str, Optional[int]]:
    """Returns (person name, database key id or None for a key from ZENO_API_TOKENS)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing key")
    key = authorization[7:].strip()
    digest = hashlib.sha256(key.encode()).hexdigest()
    for known, name in _tokens().items():
        if hmac.compare_digest(known, digest):
            return name, None
    try:
        found = store().lookup(key)
    except Exception:
        log.exception("key store unavailable")
        raise HTTPException(503, "Key check unavailable, try again shortly")
    if found:
        return found[1], found[0]
    raise HTTPException(401, "Invalid key")


_hits: Dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def check_rate(name: str) -> None:
    limit = int(os.getenv("ZENO_RATE_PER_MIN", "20"))
    now = time.time()
    with _hits_lock:
        q = _hits[name]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many requests, wait a minute", headers={"Retry-After": str(max(1, int(60 - (now - q[0])) + 1))})
        q.append(now)


class _Gemini:
    """Key and model rotation with a global calls-per-minute limit, as in scripts/align_engine/gemini_transcriber.py."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last_call = 0.0
        self.key_idx = 0
        self.model_idx = 0

    def _wait(self):
        interval = 60.0 / max(1, int(os.getenv("GEMINI_RPM", "30")))
        with self.lock:
            gap = interval - (time.time() - self.last_call)
            if gap > 0:
                time.sleep(gap)
            self.last_call = time.time()

    @staticmethod
    def _parse(raw: str) -> Dict[str, Any]:
        text = re.sub(r"\s*```$", "", re.sub(r"^```(?:json)?\s*", "", (raw or "").strip()))
        try:
            res = json.loads(text)
        except Exception:
            m = re.search(r"(\{[\s\S]*\})", text)
            if not m:
                raise
            res = json.loads(m.group(1))
        return res if isinstance(res, dict) else {"words": res if isinstance(res, list) else []}

    def transcribe(self, audio: bytes, prompt: str, duration: float, trace: Optional[List[str]] = None) -> Dict[str, Any]:
        """Try each model in order, twice each. A transcript far shorter than the audio is treated as incomplete: keep it
        only if no model does better, and mark it with "warning"."""
        from google import genai
        from google.genai import types

        keys = _gemini_keys()
        if not keys:
            raise HTTPException(503, "Service has no Gemini key configured")
        min_words = int(0.4 * duration) if duration >= 8 else 1
        best: Optional[Dict[str, Any]] = None
        count = lambda d: len(d.get("words") or str(d.get("full_transcript", "")).split())
        for model in MODELS:
            for attempt in range(max(2, len(keys))):
                try:
                    self._wait()
                    client = genai.Client(api_key=keys[self.key_idx % len(keys)], http_options=types.HttpOptions(timeout=60000))
                    resp = client.models.generate_content(
                        model=model,
                        contents=[types.Part.from_bytes(data=audio, mime_type="audio/wav"), prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
                    )
                    data = self._parse(resp.text or "{}")
                    data["model_used"] = model
                    um = getattr(resp, "usage_metadata", None)
                    if um is not None:
                        in_tok = int(getattr(um, "prompt_token_count", 0) or 0)
                        out_tok = int(getattr(um, "candidates_token_count", 0) or 0) + int(getattr(um, "thoughts_token_count", 0) or 0)
                        tot_tok = int(getattr(um, "total_token_count", 0) or (in_tok + out_tok))
                        data["usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
                    if count(data) and (best is None or count(data) > count(best)):
                        best = data
                    if count(data) >= min_words and count(data):
                        return data
                    log.info("incomplete transcript from %s: %d words for %.0fs of audio", model, count(data), duration)
                    break   # a different model may do better
                except Exception as exc:
                    self.key_idx += 1
                    if trace is not None:
                        trace.append(f"{model}: {_why(exc)}")
                    busy = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                    time.sleep(min(4.0, 1.0 + attempt + random.uniform(0.1, 0.5)) if busy else 0.3)
        if best is not None:
            best["warning"] = f"incomplete: {count(best)} words for {duration:.0f}s of audio"
            return best
        raise HTTPException(502, "Transcription failed upstream")


_gemini = _Gemini()


def _why(exc: BaseException) -> str:
    """A short plain-language reason for a Gemini failure: no key, no text, only the kind of problem."""
    s = f"{type(exc).__name__} {exc}"
    for needle, words in (("504", "timed out at Google (504)"), ("DEADLINE", "timed out at Google"), ("timed out", "timed out"), ("Timeout", "timed out"),
                          ("429", "rate limit or quota (429)"), ("RESOURCE_EXHAUSTED", "rate limit or quota"), ("503", "Google overloaded (503)"),
                          ("500", "Google error (500)"), ("UNAVAILABLE", "Google unavailable"), ("403", "key not allowed (403)"),
                          ("401", "key rejected (401)"), ("SAFETY", "blocked by safety filter"), ("JSON", "answer was not valid JSON")):
        if needle in s:
            return words
    return type(exc).__name__


def _log_transcription(key_id, name: str, **fields: Any) -> None:
    """Never lets monitoring break a transcription."""
    try:
        store().log_transcription(key_id, name, **fields)
    except Exception:
        log.exception("could not record transcription event")


def _allowed_domains() -> List[str]:
    """Email domains allowed to sign in: gosupernova.live unless ZENO_ALLOWED_EMAIL_DOMAINS lists others (comma-separated)."""
    raw = os.getenv("ZENO_ALLOWED_EMAIL_DOMAINS") or os.getenv("ZENO_ALLOWED_EMAIL_DOMAIN") or "gosupernova.live"
    domains = [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]
    return domains or ["gosupernova.live"]


def _admin_emails() -> List[str]:
    raw = os.getenv("ZENO_ADMIN_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _is_allowed_email(email: str) -> bool:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    if email in _admin_emails():
        return True
    domain = email.rsplit("@", 1)[1]
    return domain in _allowed_domains()


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        backend, db_ok = store().backend.split(":")[0], True
    except Exception:
        backend, db_ok = "none", False
    domains = _allowed_domains()
    client_id = (os.getenv("ZENO_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    return {
        "ok": True,
        "gemini_configured": bool(_gemini_keys()),
        "key_store": backend,
        "key_store_ok": db_ok,
        "google_oauth_enabled": bool(client_id),
        "allowed_domains": domains,
        "signup_code_required": bool(os.getenv("ZENO_SIGNUP_CODE")),
        "email_domain": domains[0] if domains else "gosupernova.live",
        "admin_enabled": bool(os.getenv("ZENO_ADMIN_PASSWORD") or client_id or _admin_emails()),
    }


@app.get("/api/auth/config")
def auth_config() -> Dict[str, Any]:
    domains = _allowed_domains()
    client_id = (os.getenv("ZENO_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    return {
        "google_client_id": client_id,
        "google_oauth_enabled": bool(client_id),
        "allowed_domains": domains,
        "primary_domain": domains[0] if domains else "gosupernova.live",
        "signup_code_required": bool(os.getenv("ZENO_SIGNUP_CODE")),
        "admin_password_enabled": bool(os.getenv("ZENO_ADMIN_PASSWORD")),
        "admin_allow_domain": os.getenv("ZENO_ADMIN_ALLOW_DOMAIN", "0").strip().lower() in ("1", "true", "yes"),
    }


@app.get("/v1/me")
def get_me(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    name, key_id = authenticate(authorization)
    email = store().get_key_email(key_id) if key_id is not None and hasattr(store(), "get_key_email") else ""
    return {"ok": True, "name": name, "email": email, "key_id": key_id}


_ping_cache: Dict[str, Any] = {"at": 0.0, "result": None}


def _gemini_ping() -> Dict[str, Any]:
    """One tiny text request, trying each model and configured key in turn, so we know a Gemini key and the network path work (cached for 60 s when ok)."""
    now = time.time()
    if _ping_cache["result"] is not None and _ping_cache["result"].get("ok") and now - _ping_cache["at"] < 60:
        return _ping_cache["result"]
    keys = _gemini_keys()
    if not keys:
        res: Dict[str, Any] = {"ok": False, "detail": "no Gemini key is configured on the server"}
    else:
        try:
            from google import genai
            from google.genai import types

            res = {"ok": False, "detail": "no model answered"}
            failed = []
            answered = False
            for model in MODELS:   # same order as a real transcription: one busy model must not fail the whole check
                t0 = time.time()
                model_ok = False
                for offset in range(len(keys)):
                    k_idx = (_gemini.key_idx + offset) % len(keys)
                    try:
                        client = genai.Client(api_key=keys[k_idx], http_options=types.HttpOptions(timeout=15000))
                        reply = client.models.generate_content(model=model, contents="Reply with the single word: ok")
                        if (reply.text or "").strip():
                            _gemini.key_idx = k_idx
                            note = f" ({', '.join(failed)} did not answer)" if failed else ""
                            res = {"ok": True, "detail": f"{model} answered in {time.time() - t0:.1f}s{note}"}
                            model_ok = True
                            answered = True
                            break
                    except Exception:
                        continue
                if answered:
                    break
                if not model_ok:
                    failed.append(model)
            if not answered:
                res = {"ok": False, "detail": f"Gemini call failed on every model ({', '.join(failed)})"}
        except Exception as exc:
            res = {"ok": False, "detail": f"Gemini call failed ({type(exc).__name__})"}
    if res.get("ok"):
        _ping_cache.update(at=now, result=res)
    else:
        _ping_cache.update(at=0.0, result=None)
    return res


@app.post("/v1/selftest")
def selftest(file: Optional[UploadFile] = File(None), authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Pre-job check from a team PC: is my key accepted, does a small WAV pass the same checks as a real upload,
    is the key store up, and can the server reach Gemini. It does not transcribe, so it costs almost nothing."""
    name, _key_id = authenticate(authorization)
    check_rate(name)
    checks: List[Dict[str, Any]] = [{"name": "key accepted", "ok": True, "detail": f"signed in as {name}"}]

    if file is not None:
        limit = int(float(os.getenv("ZENO_MAX_UPLOAD_MB", "25")) * 1024 * 1024)
        audio = file.file.read(limit + 1)
        try:
            with wave.open(io.BytesIO(audio)) as wf:
                secs, rate = wf.getnframes() / float(wf.getframerate()), wf.getframerate()
            wav_ok = len(audio) <= limit and secs >= 0.5
            detail = f"{secs:.1f}s at {rate} Hz, {len(audio) // 1024} KB received"
        except Exception:
            wav_ok, detail = False, "the server could not read the WAV that was sent"
        checks.append({"name": "audio upload accepted", "ok": wav_ok, "detail": detail})

    try:
        checks.append({"name": "key store", "ok": True, "detail": store().backend.split(":")[0]})
    except Exception:
        checks.append({"name": "key store", "ok": False, "detail": "unavailable"})
    ping = _gemini_ping()
    checks.append({"name": "Gemini reachable", "ok": ping["ok"], "detail": ping["detail"]})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


@app.post("/v1/transcribe")
def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language_hint: str = Form("auto"),
    target_script_lines: str = Form("[]"),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    name, key_id = authenticate(authorization)
    check_rate(name)
    limit = int(float(os.getenv("ZENO_MAX_UPLOAD_MB", "25")) * 1024 * 1024)
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit + 4096:
        raise HTTPException(413, "Audio too large")
    audio = file.file.read(limit + 1)
    if len(audio) > limit:
        raise HTTPException(413, "Audio too large")
    try:
        with wave.open(io.BytesIO(audio)) as wf:
            duration = wf.getnframes() / float(wf.getframerate())
    except Exception:
        raise HTTPException(400, "Send a valid WAV file")
    if duration < 0.5:
        raise HTTPException(400, "Audio is too short")
    try:
        lines = [str(x) for x in json.loads(target_script_lines)][:40]
    except Exception:
        raise HTTPException(400, "target_script_lines must be a JSON list of strings")

    started = time.time()
    cache_key = "|".join([
        hashlib.sha256(audio).hexdigest(), PROMPT_VERSION, language_hint or "auto",
        hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()[:16],
    ])
    result = None
    trace: List[str] = []
    try:
        cached = store().get_cached_transcript(cache_key)
        if cached:   # same audio, prompt and script lines: no Gemini call, no tokens
            result = {**cached, "cached": True, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
    except Exception:
        log.exception("transcript cache unavailable")
    if result is None:
        try:
            result = _gemini.transcribe(audio, build_transcribe_prompt(lines, language_hint=language_hint or "auto"), duration, trace)
        except HTTPException as exc:
            log.info("user=%s bytes=%d status=%d seconds=%.1f", name, len(audio), exc.status_code, time.time() - started)
            _log_transcription(key_id, name, status="failed", http_status=exc.status_code, failed_models="; ".join(trace), audio_seconds=duration,
                               size=len(audio), seconds=time.time() - started, reason=(trace[-1].split(": ", 1)[-1] if trace else str(exc.detail)))
            raise
        if result.get("words") and not result.get("warning"):   # an incomplete transcript is never cached
            try:
                store().put_cached_transcript(cache_key, result)
            except Exception:
                log.exception("could not cache transcript")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    tot_tok = int(usage.get("total_tokens") or (in_tok + out_tok))
    if key_id is not None:
        try:
            store().record_use(key_id, duration, input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok)
        except Exception:
            log.exception("could not record usage")
    _log_transcription(key_id, name, status="incomplete" if result.get("warning") else "ok", model=str(result.get("model_used") or ""),
                       failed_models="; ".join(trace), words=len(result.get("words") or []), audio_seconds=duration, size=len(audio),
                       seconds=time.time() - started, in_tokens=in_tok, out_tokens=out_tok, cached=bool(result.get("cached")),
                       reason=str(result.get("warning") or ""))
    log.info("user=%s bytes=%d status=200 model=%s words=%d in_tok=%d out_tok=%d seconds=%.1f%s", name, len(audio), result.get("model_used"), len(result.get("words") or []), in_tok, out_tok, time.time() - started, " WARNING=" + result["warning"] if result.get("warning") else "")
    return result


# ---------------------------------------------------------------- knowledge: formats and reference learnings kept in the database
# Knowledge is published here once and every computer pulls it at the start of a job, so a new format or reference needs
# no commit and no redeploy. The heavy work (alignment, OpenCV, FFmpeg) stays on each computer; the server only holds
# what was learned and answers "which format does this scene wording describe?".
EMBED_MODEL = os.getenv("ZENO_EMBED_MODEL", "gemini-embedding-001")


def _embed(text: str) -> Optional[List[float]]:
    """Embedding of a short text through Gemini, or None when no key or the call fails (matching then uses words only)."""
    keys = _gemini_keys()
    if not keys or not text.strip():
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=keys[0], http_options=types.HttpOptions(timeout=15000))
        res = client.models.embed_content(model=EMBED_MODEL, contents=text[:2000])
        return [float(x) for x in res.embeddings[0].values]
    except Exception:
        log.warning("embedding unavailable; matching by words only")
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = sum(x * x for x in a) ** 0.5, sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _word_score(query: str, item: Dict[str, Any]) -> float:
    q = query.lower()
    names = item["payload"].get("names") or []
    alias = sum(3.0 for n in names if n and re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", q))
    overlap = len(set(re.findall(r"[a-z0-9]+", q)) & set(re.findall(r"[a-z0-9]+", item["match_text"].lower())))
    return alias + 0.2 * overlap


class KnowledgeBody(BaseModel):
    payload: Dict[str, Any]
    text: str = ""   # what the item is about, in words; this is what gets matched and embedded


@app.put("/admin/api/knowledge/{kind}/{name:path}")
def admin_put_knowledge(
    kind: str, name: str, body: KnowledgeBody,
    authorization: Optional[str] = Header(None),
    x_zeno_admin: Optional[str] = Header(None),
    x_admin_password: Optional[str] = Header(None),
) -> Dict[str, Any]:
    admin = _check_admin(authorization, x_admin_password)
    if x_zeno_admin != "1":
        raise HTTPException(400, "Missing admin header")
    if kind not in KeyStore.KNOWLEDGE_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KeyStore.KNOWLEDGE_KINDS)}")
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,80}", name) or ".." in name:
        raise HTTPException(400, "Invalid name")
    if len(json.dumps(body.payload)) > 200_000:
        raise HTTPException(413, "Payload too large")
    text = body.text or " ".join(str(body.payload.get(k, "")) for k in ("description", "when") if body.payload.get(k)) or name
    match_text = " ".join([text] + [str(n) for n in body.payload.get("names", [])])
    updated = store().put_knowledge(kind, name, body.payload, match_text, _embed(match_text), admin.get("email") or admin.get("name") or "admin")
    return {"ok": True, "kind": kind, "name": name, "updated_at": updated}


@app.get("/v1/knowledge")
def get_knowledge(since: float = 0.0, kind: Optional[str] = None, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Everything published after `since` (a timestamp from the previous call). A computer asks at the start of each job."""
    authenticate(authorization)
    return {"now": time.time(), "items": store().knowledge_since(since, kind)}


class MatchBody(BaseModel):
    text: str
    kind: str = "format"
    k: int = 3


@app.post("/v1/match")
def match_knowledge(body: MatchBody, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Which stored format does this scene wording describe? Embedding similarity when the server could embed the text,
    otherwise alias and word matching. Send only the direction wording (for example 'floating window, horizontal split screen'),
    never the script, footage or audio."""
    name, _ = authenticate(authorization)
    check_rate(name)
    text = body.text.strip()[:500]
    if not text or body.kind not in KeyStore.KNOWLEDGE_KINDS:
        raise HTTPException(400, "Send some wording and a valid kind")
    items = store().knowledge_for_match(body.kind)
    qv = _embed(text)
    if qv:   # an item published while embedding was unavailable gets its embedding now, so one gap never turns off semantic matching
        for it in items:
            if it["embedding"] is None:
                emb = _embed(it["match_text"])
                if emb:
                    it["embedding"] = emb
                    try:
                        store().set_knowledge_embedding(body.kind, it["name"], emb)
                    except Exception:
                        log.exception("could not store embedding")
    ranked = []
    for it in items:
        if qv and it["embedding"]:
            ranked.append((_cosine(qv, it["embedding"]), "embedding", it))
        else:
            ranked.append((_word_score(text, it), "words", it))
    # Mixed scores are not comparable: if every item could be scored by embedding use that, else use words for all
    if ranked and any(m == "words" for _, m, _ in ranked):
        ranked = [(_word_score(text, it), "words", it) for it in items]
    ranked.sort(key=lambda r: -r[0])
    return {"matches": [{"name": it["name"], "score": round(sc, 4), "method": m, "layout": it["payload"].get("layout"),
                         "description": it["payload"].get("description", "")[:200]} for sc, m, it in ranked[: max(1, min(body.k, 10))]]}


MIN_FORMAT_JOBS = int(os.getenv("ZENO_FORMAT_MIN_JOBS", "5"))


class FormatMetric(BaseModel):
    format: str
    segments: float = 0
    seconds: float = 0
    mean_shot_sec: float = 0
    zoom_in_share: float = 0
    transitions_per_min: float = 0
    marks: float = 0
    strike_banners: float = 0


class ObservationBody(BaseModel):
    style: str
    script_hash: str
    verified: bool = False
    video_seconds: float = 0
    formats: List[FormatMetric]


@app.post("/v1/observations")
def add_observation(body: ObservationBody, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """After a job: the numbers (never the script or footage) for each format the plan used."""
    name, key_id = authenticate(authorization)
    check_rate(name)
    style = re.sub(r"[^a-z0-9_-]", "", body.style.lower())[:40] or "unspecified"
    if len(body.formats) > 12 or not re.fullmatch(r"[0-9a-f]{8,64}", body.script_hash):
        raise HTTPException(400, "Invalid observation")
    formats = [f.model_dump() for f in body.formats]
    for f in formats:
        f["format"] = re.sub(r"[^a-z0-9_-]", "", f["format"].lower())[:30] or "?"
    try:
        store().add_format_observation(key_id, style, body.script_hash, body.verified, body.video_seconds, {"formats": formats})
    except Exception:
        log.exception("could not store observation")
        raise HTTPException(503, "Could not store observation")
    return {"ok": True}


@app.get("/v1/formats")
def approved_formats(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Recipes an editor has approved. Computers copy these into styles/formats/team/."""
    authenticate(authorization)
    return {"formats": store().approved_formats()}


@app.get("/admin/api/formats")
def admin_formats(authorization: Optional[str] = Header(None), x_admin_password: Optional[str] = Header(None)) -> JSONResponse:
    require_admin(authorization, x_admin_password)
    return JSONResponse({"min_jobs": MIN_FORMAT_JOBS, "formats": store().aggregate_formats()}, headers=NO_STORE)


@app.post("/admin/api/formats/{style}/{fmt}/approve")
def admin_approve_format(
    style: str, fmt: str,
    authorization: Optional[str] = Header(None),
    x_zeno_admin: Optional[str] = Header(None),
    x_admin_password: Optional[str] = Header(None),
) -> Dict[str, Any]:
    admin = _check_admin(authorization, x_admin_password)
    if x_zeno_admin != "1":
        raise HTTPException(400, "Missing admin header")
    row = next((r for r in store().aggregate_formats() if r["style"] == style and r["format"] == fmt), None)
    if row is None:
        raise HTTPException(404, "No observations for that format")
    if row["n_jobs"] < MIN_FORMAT_JOBS:
        raise HTTPException(409, f"Needs {MIN_FORMAT_JOBS} jobs with different scripts; has {row['n_jobs']}")
    store().approve_format(style, fmt, row["stats"], row["n_jobs"], admin.get("email") or admin.get("name") or "admin")
    return {"approved": True, "n_jobs": row["n_jobs"]}


@app.post("/v1/telemetry")
async def record_telemetry(request: Request, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Record a completed video edit run (agent name/model, per-clip process & tokens, EDL diff, learned rules) into Postgres."""
    name, key_id = authenticate(authorization)
    check_rate(name)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
    except Exception:
        raise HTTPException(400, "Invalid telemetry JSON")
    try:
        run_id = store().record_run(key_id, name, payload)
    except Exception:
        log.exception("could not record telemetry run")
        raise HTTPException(503, "Could not store telemetry")
    log.info(
        "telemetry run_id=%d user=%s project=%s agent=%s/%s clips=%s grand_tokens=%s",
        run_id, name, payload.get("project"), payload.get("agent_name"), payload.get("agent_model"),
        payload.get("clips_count"), payload.get("grand_total_tokens"),
    )
    return {"ok": True, "run_id": run_id}


class LearnedRuleBody(BaseModel):
    rule: str = ""
    rule_text: str = ""
    category: str = "general"
    language: str = "all"
    lang: str = ""
    style: str = "micronarrative"
    project: str = ""
    source: str = "cli_remember"
    metadata: Dict[str, Any] = {}


@app.get("/v1/learnings")
def get_learnings(
    language: Optional[str] = None,
    lang: Optional[str] = None,
    style: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(None),
    x_admin_password: Optional[str] = Header(None),
) -> JSONResponse:
    """Return aggregated editorial learnings across all team runs: learned_rules, edl_diffs, language_patterns, assets_used."""
    try:
        authenticate(authorization)
    except HTTPException:
        _check_admin(authorization, x_admin_password)

    ks = store()
    data = ks.get_learnings(limit=limit, lang=lang or language)
    return JSONResponse(data, headers=NO_STORE)


@app.post("/v1/learnings")
@app.post("/v1/learnings/rule")
def post_learned_rule(
    body: LearnedRuleBody,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Store a new learned editorial rule pushed from `zeno.py --remember` into the shared team database."""
    name, key_id = authenticate(authorization)
    check_rate(name)
    rule_text = (body.rule or body.rule_text or "").strip()
    if not rule_text:
        raise HTTPException(400, "Rule text cannot be empty")
    lang_code = (body.lang or body.language or "all").strip() or "all"
    try:
        rule_id = store().record_learned_rule(
            key_id=key_id,
            editor_name=name,
            rule=rule_text,
            category=body.category.strip() or "general",
            language=lang_code,
            project=body.project.strip(),
            source=body.source.strip() or "cli_remember",
            metadata=body.metadata,
        )
    except Exception:
        log.exception("could not record learned rule")
        raise HTTPException(503, "Could not store learned rule")
    log.info("learned_rule id=%s user=%s lang=%s category=%s", rule_id, name, lang_code, body.category)
    return {"ok": True, "rule_id": rule_id, "editor_name": name, "rule": rule_text, "language": lang_code}


# ---------------------------------------------------------------- key signup, Google OAuth & admin pages
NO_STORE = {"Cache-Control": "no-store"}
_signup_hits: Dict[str, deque] = defaultdict(deque)
_cli_pending_sessions: Dict[str, Dict[str, Any]] = {}


class SignupBody(BaseModel):
    name: str
    email: str = ""   # optional: when empty it is built from the name
    code: str = ""
    cli_state: str = ""


class GoogleAuthBody(BaseModel):
    credential: str = ""      # Google Identity Services ID token (JWT)
    code: str = ""            # OAuth2 authorization code
    redirect_uri: str = ""    # Used when exchanging authorization code
    name: str = ""
    cli_state: str = ""


def _store_cli_pending(cli_state: str, ip: str, payload: Dict[str, Any]) -> None:
    now = time.time()
    with _hits_lock:
        expired = [k for k, v in _cli_pending_sessions.items() if now - float(v.get("created_at", 0)) > 600]
        for k in expired:
            _cli_pending_sessions.pop(k, None)
        entry = {**payload, "created_at": now}
        if cli_state and len(cli_state.strip()) >= 6:
            _cli_pending_sessions[cli_state.strip()] = entry
        if ip:
            _cli_pending_sessions[f"ip:{ip}"] = entry


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _check_signup_rate(ip: str, record: bool = False) -> None:
    """Guard against abuse and code guessing: 5 attempts per hour per address, and a cap on keys per hour overall."""
    now = time.time()
    cap = int(os.getenv("ZENO_MAX_SIGNUPS_PER_HOUR", "30"))
    with _hits_lock:
        for q in (_signup_hits[ip], _signup_hits["*"]):
            while q and now - q[0] > 3600:
                q.popleft()
        if record:
            _signup_hits[ip].append(now)
            _signup_hits["*"].append(now)
        elif len(_signup_hits[ip]) >= 5 or len(_signup_hits["*"]) >= cap:
            raise HTTPException(429, "Too many keys requested, try again in an hour")


def _verify_google_identity(body: GoogleAuthBody) -> Dict[str, str]:
    """Verify a Google ID token (or exchange an auth code for one) and check it was issued to this app's client id."""
    import httpx

    id_token = body.credential.strip()
    client_id = (os.getenv("ZENO_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("ZENO_GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id:
        raise HTTPException(503, "Google sign-in is not configured on this server")

    with httpx.Client(timeout=10.0) as client:
        if body.code.strip() and not id_token:
            if not client_secret:
                raise HTTPException(400, "Google OAuth code exchange is not configured on this server")
            token_resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": body.code.strip(),
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": body.redirect_uri.strip() or "postmessage",
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise HTTPException(401, "Invalid Google authorization code")
            id_token = token_resp.json().get("id_token", "")
        if not id_token:
            raise HTTPException(400, "Missing Google credential")
        resp = client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
        if resp.status_code != 200:
            raise HTTPException(401, "Invalid Google ID token")
        info = resp.json()
        if info.get("aud") != client_id:
            raise HTTPException(401, "Google ID token audience mismatch")

    email = str(info.get("email") or "").strip().lower()
    verified = str(info.get("email_verified", "false")).lower() in ("true", "1")
    if not email or not verified:
        raise HTTPException(401, "Google account email is not verified")

    if not _is_allowed_email(email):
        raise HTTPException(403, "Only " + " / ".join("@" + d for d in _allowed_domains()) + " accounts are permitted")

    name = (
        str(info.get("name") or "").strip()
        or body.name.strip()
        or email.split("@", 1)[0].replace(".", " ").title()
    )
    return {
        "email": email,
        "name": name[:60],
        "sub": str(info.get("sub") or ""),
        "picture": str(info.get("picture") or ""),
    }


@app.get("/", response_class=HTMLResponse)
def signup_page() -> HTMLResponse:
    return HTMLResponse(SIGNUP_PAGE, headers=NO_STORE)


@app.post("/api/auth/google")
def google_auth_login(body: GoogleAuthBody, request: Request) -> JSONResponse:
    ip = _client_ip(request)
    profile = _verify_google_identity(body)
    email = profile["email"]
    name = profile["name"]
    try:
        res = store().create_or_get_google_key(name, email, google_sub=profile.get("sub", ""))
    except Exception:
        log.exception("key store unavailable during google auth")
        raise HTTPException(503, "Could not issue key right now, try again shortly")

    out_payload = {
        "key": res["key"],
        "token": res["key"],
        "name": name,
        "email": email,
        "prefix": res["prefix"],
        "key_id": res["key_id"],
        "picture": profile.get("picture", ""),
    }
    _store_cli_pending(body.cli_state, ip, out_payload)
    log.info("google auth key issued for %s <%s>", name, email)
    return JSONResponse(out_payload, headers=NO_STORE)


@app.get("/api/auth/cli-poll")
def poll_cli_session(request: Request, cli_state: str = Query(""), use_ip_fallback: int = Query(0)) -> JSONResponse:
    ip = _client_ip(request)
    now = time.time()
    with _hits_lock:
        st = cli_state.strip()
        if st and st in _cli_pending_sessions:
            entry = _cli_pending_sessions.pop(st)
            if now - float(entry.get("created_at", 0)) <= 600:
                return JSONResponse({"ready": True, **entry}, headers=NO_STORE)
        if use_ip_fallback and ip and f"ip:{ip}" in _cli_pending_sessions:
            entry = _cli_pending_sessions.get(f"ip:{ip}", {})
            if now - float(entry.get("created_at", 0)) <= 120:
                _cli_pending_sessions.pop(f"ip:{ip}", None)
                return JSONResponse({"ready": True, **entry}, headers=NO_STORE)
    return JSONResponse({"ready": False}, headers=NO_STORE)


@app.post("/api/keys")
def create_key(body: SignupBody, request: Request) -> JSONResponse:
    code = os.getenv("ZENO_SIGNUP_CODE", "")
    if not code:
        raise HTTPException(403, "Name-only signup is off. Sign in with Google on the Zeno page.")
    ip = _client_ip(request)
    _check_signup_rate(ip)
    if not hmac.compare_digest(body.code.encode(), code.encode()):
        _check_signup_rate(ip, record=True)
        raise HTTPException(403, "Wrong team code")
    domains = _allowed_domains()
    primary_domain = domains[0] if domains else "gosupernova.live"
    raw_input = body.name.strip()
    supplied_email = body.email.strip().lower()
    if "@" in raw_input and not supplied_email:
        supplied_email = raw_input.lower()
        name = raw_input.split("@", 1)[0].replace(".", " ").title()
    else:
        name = re.sub(r"\s*@[a-z0-9.-]+\s*$", "", raw_input, flags=re.I)

    if not re.fullmatch(r"[^\W\d_][\w .'-]{0,39}", name):
        raise HTTPException(400, "Enter your name (letters, spaces, . ' - only, up to 40)")
    if supplied_email:
        email = supplied_email
    else:
        local = re.sub(r"[^a-z0-9._-]", "", re.sub(r"\s+", ".", name.lower()))
        email = f"{local}@{primary_domain}"
    if not _is_allowed_email(email) or not re.fullmatch(r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]+", email):
        allowed_str = " or @".join(domains)
        raise HTTPException(400, f"Only @{allowed_str} accounts are permitted")
    try:
        key = store().create(name, email)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception:
        log.exception("key store unavailable")
        raise HTTPException(503, "Could not create a key right now, try again shortly")
    _check_signup_rate(ip, record=True)
    out_payload = {"key": key, "token": key, "name": name, "email": email, "prefix": key[:12]}
    _store_cli_pending(body.cli_state, ip, out_payload)
    log.info("key created for %s <%s>", name, email)
    return JSONResponse(out_payload, headers=NO_STORE)


def _check_admin(
    authorization: Optional[str] = None,
    x_admin_password: Optional[str] = None,
) -> Dict[str, str]:
    password = os.getenv("ZENO_ADMIN_PASSWORD", "").strip()
    allow_domain = os.getenv("ZENO_ADMIN_ALLOW_DOMAIN", "0").strip().lower() in ("1", "true", "yes")
    admin_list = _admin_emails()

    if password and x_admin_password and hmac.compare_digest(x_admin_password.strip().encode(), password.encode()):
        return {"name": "admin", "email": "admin@gosupernova.live", "via": "password"}

    if password and authorization and authorization.lower().startswith("basic "):
        try:
            supplied = base64.b64decode(authorization[6:]).decode().split(":", 1)[1]
        except Exception:
            supplied = ""
        if supplied and hmac.compare_digest(supplied.encode(), password.encode()):
            return {"name": "admin", "email": "admin@gosupernova.live", "via": "basic"}

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token.startswith("zeno_"):
            found = store().lookup(token)
            if found:
                key_id, key_name = found[0], found[1]
                key_email = store().get_key_email(key_id)
                if key_email and (key_email in admin_list or (allow_domain and _is_allowed_email(key_email))):
                    return {"name": key_name, "email": key_email, "via": "zeno_key"}
                raise HTTPException(403, "Your account does not have admin access")
        elif token.count(".") == 2:
            profile = _verify_google_identity(GoogleAuthBody(credential=token))
            email = profile["email"]
            if email in admin_list or (allow_domain and _is_allowed_email(email)):
                return {"name": profile["name"], "email": email, "via": "google_jwt"}
            raise HTTPException(403, "Your Google account does not have admin access")

    if not password and not (os.getenv("ZENO_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")):
        raise HTTPException(503, "Admin is not enabled. Set ZENO_ADMIN_PASSWORD or ZENO_GOOGLE_CLIENT_ID.")
    raise HTTPException(401, "Admin password required", headers={"WWW-Authenticate": 'Basic realm="Zeno admin"'})


def require_admin(authorization: Optional[str], x_admin_password: Optional[str] = None) -> None:
    _check_admin(authorization, x_admin_password)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(authorization: Optional[str] = Header(None), x_admin_password: Optional[str] = Header(None)) -> HTMLResponse:
    require_admin(authorization, x_admin_password)
    return HTMLResponse(ADMIN_PAGE, headers=NO_STORE)


@app.get("/admin/api/keys")
def admin_keys(authorization: Optional[str] = Header(None), x_admin_password: Optional[str] = Header(None)) -> JSONResponse:
    require_admin(authorization, x_admin_password)
    ks = store()
    return JSONResponse(
        {"keys": ks.list(), "runs": ks.list_runs(), "learnings": ks.get_learnings(limit=100)},
        headers=NO_STORE,
    )


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/usage", response_class=HTMLResponse)
def usage_page() -> HTMLResponse:
    """The page itself holds no data: it asks for a company Google sign-in and then reads /api/usage."""
    return HTMLResponse(USAGE_PAGE, headers=NO_STORE)


def _check_viewer(authorization: Optional[str], x_admin_password: Optional[str] = None) -> Dict[str, str]:
    """Anyone with a company account may READ the usage log: no admin list, no password. Admins (password or listed) pass as before.
    Revoking keys and approving recipes still need `_check_admin`."""
    try:
        return _check_admin(authorization, x_admin_password)
    except HTTPException as exc:
        if exc.status_code not in (401, 403):
            raise
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token.startswith("zeno_"):
            found = store().lookup(token)
            email = store().get_key_email(found[0]) if found else ""
            if email and _is_allowed_email(email):
                return {"name": found[1], "email": email, "via": "zeno_key"}
            raise HTTPException(403, "Only company accounts can see the usage log")
        if token.count(".") == 2:
            profile = _verify_google_identity(GoogleAuthBody(credential=token))
            if _is_allowed_email(profile["email"]):
                return {"name": profile["name"], "email": profile["email"], "via": "google_jwt"}
            raise HTTPException(403, "Only company accounts can see the usage log")
    raise HTTPException(401, "Sign in with your company Google account")


@app.get("/api/usage")
def usage_log(authorization: Optional[str] = Header(None), x_admin_password: Optional[str] = Header(None)) -> JSONResponse:
    """Every editor's runs as plain numbers, plus each key's server-counted totals. No plan files and no secrets."""
    _check_viewer(authorization, x_admin_password)
    ks = store()
    keep = ("name", "email", "created_at", "last_used_at", "revoked_at", "requests", "audio_seconds", "input_tokens", "output_tokens", "total_tokens")
    return JSONResponse(
        {"keys": [{k: row.get(k) for k in keep} for row in ks.list()], "runs": ks.usage_runs(), "now": time.time(),
         "transcriptions": ks.transcription_events(since=time.time() - 30 * 86400)},
        headers=NO_STORE,
    )


@app.post("/admin/api/keys/{key_id}/revoke")
def admin_revoke(
    key_id: int,
    authorization: Optional[str] = Header(None),
    x_zeno_admin: Optional[str] = Header(None),
    x_admin_password: Optional[str] = Header(None),
) -> Dict[str, Any]:
    require_admin(authorization, x_admin_password)
    if x_zeno_admin != "1":
        raise HTTPException(400, "Missing admin header")
    return {"revoked": store().revoke(key_id)}
