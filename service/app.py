"""Zeno transcription service.

One job: turn audio into a verbatim transcript with Gemini, so the Gemini key lives only here.
Team PCs send audio with a personal key; they never see the Gemini key.

Run locally:   uvicorn app:app --port 8000   (from the service/ folder)
Environment:
    GEMINI_API_KEY (and optional GEMINI_API_KEY_1..4)   the Gemini key(s), kept secret
    DATABASE_URL         Postgres address (Railway: reference the Postgres service). Without it a local SQLite file is used.
    ZENO_SIGNUP_CODE     optional team code asked on the key page; when unset, anyone who can open the page can generate a key
    ZENO_ALLOWED_EMAIL_DOMAIN  only emails at this domain can generate a key (default gosupernova.live)
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
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from keystore import KeyStore
from prompt import build_transcribe_prompt
from ui import ADMIN_PAGE, SIGNUP_PAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("zeno.service")

# Lite first: on a 93 s clip gemini-2.5-flash timed out every time while lite answered in about 4 s with a complete transcript.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
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
            raise HTTPException(429, "Too many requests, wait a minute")
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

    def transcribe(self, audio: bytes, prompt: str, duration: float) -> Dict[str, Any]:
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
            for attempt in range(2):
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
                    if count(data) and (best is None or count(data) > count(best)):
                        best = data
                    if count(data) >= min_words and count(data):
                        return data
                    log.info("incomplete transcript from %s: %d words for %.0fs of audio", model, count(data), duration)
                    break   # a different model may do better
                except Exception as exc:
                    self.key_idx += 1
                    busy = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                    time.sleep(min(4.0, 1.0 + attempt + random.uniform(0.1, 0.5)) if busy else 0.3)
        if best is not None:
            best["warning"] = f"incomplete: {count(best)} words for {duration:.0f}s of audio"
            return best
        raise HTTPException(502, "Transcription failed upstream")


_gemini = _Gemini()


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        backend, db_ok = store().backend.split(":")[0], True
    except Exception:
        backend, db_ok = "none", False
    return {"ok": True, "gemini_configured": bool(_gemini_keys()), "key_store": backend, "key_store_ok": db_ok,
            "signup_code_required": bool(os.getenv("ZENO_SIGNUP_CODE")), "admin_enabled": bool(os.getenv("ZENO_ADMIN_PASSWORD"))}


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
    try:
        result = _gemini.transcribe(audio, build_transcribe_prompt(lines, language_hint=language_hint or "auto"), duration)
    except HTTPException as exc:
        log.info("user=%s bytes=%d status=%d seconds=%.1f", name, len(audio), exc.status_code, time.time() - started)
        raise
    if key_id is not None:
        try:
            store().record_use(key_id, duration)
        except Exception:
            log.exception("could not record usage")
    log.info("user=%s bytes=%d status=200 model=%s words=%d seconds=%.1f%s", name, len(audio), result.get("model_used"), len(result.get("words") or []), time.time() - started, " WARNING=" + result["warning"] if result.get("warning") else "")
    return result


# ---------------------------------------------------------------- key signup and admin pages
NO_STORE = {"Cache-Control": "no-store"}
_signup_hits: Dict[str, deque] = defaultdict(deque)


class SignupBody(BaseModel):
    name: str
    email: str
    code: str = ""


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


@app.get("/", response_class=HTMLResponse)
def signup_page() -> HTMLResponse:
    return HTMLResponse(SIGNUP_PAGE, headers=NO_STORE)


@app.post("/api/keys")
def create_key(body: SignupBody, request: Request) -> JSONResponse:
    code = os.getenv("ZENO_SIGNUP_CODE", "")
    ip = _client_ip(request)
    _check_signup_rate(ip)
    if code and not hmac.compare_digest(body.code.encode(), code.encode()):
        _check_signup_rate(ip, record=True)
        raise HTTPException(403, "Wrong team code")
    name = body.name.strip()
    if not re.fullmatch(r"[^\W\d_][\w .'-]{0,39}", name):
        raise HTTPException(400, "Enter your name (letters, spaces, . ' - only, up to 40)")
    email = body.email.strip().lower()
    domain = os.getenv("ZENO_ALLOWED_EMAIL_DOMAIN", "gosupernova.live").strip().lower().lstrip("@")
    if not re.fullmatch(r"[a-z0-9._%+-]{1,64}@" + re.escape(domain), email):
        raise HTTPException(400, f"Use your company email (name@{domain})")
    try:
        key = store().create(name, email)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception:
        log.exception("key store unavailable")
        raise HTTPException(503, "Could not create a key right now, try again shortly")
    _check_signup_rate(ip, record=True)
    log.info("key created for %s <%s>", name, email)
    return JSONResponse({"key": key}, headers=NO_STORE)


def require_admin(authorization: Optional[str]) -> None:
    password = os.getenv("ZENO_ADMIN_PASSWORD", "")
    if not password:
        raise HTTPException(503, "Admin is not enabled. Set ZENO_ADMIN_PASSWORD.")
    supplied = ""
    if authorization and authorization.lower().startswith("basic "):
        try:
            supplied = base64.b64decode(authorization[6:]).decode().split(":", 1)[1]
        except Exception:
            supplied = ""
    if not supplied or not hmac.compare_digest(supplied.encode(), password.encode()):
        raise HTTPException(401, "Admin password required", headers={"WWW-Authenticate": 'Basic realm="Zeno admin"'})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(authorization: Optional[str] = Header(None)) -> HTMLResponse:
    require_admin(authorization)
    return HTMLResponse(ADMIN_PAGE, headers=NO_STORE)


@app.get("/admin/api/keys")
def admin_keys(authorization: Optional[str] = Header(None)) -> JSONResponse:
    require_admin(authorization)
    return JSONResponse({"keys": store().list()}, headers=NO_STORE)


@app.post("/admin/api/keys/{key_id}/revoke")
def admin_revoke(key_id: int, authorization: Optional[str] = Header(None), x_zeno_admin: Optional[str] = Header(None)) -> Dict[str, Any]:
    require_admin(authorization)
    if x_zeno_admin != "1":   # the browser resends the password on its own, so a custom header blocks cross-site forms
        raise HTTPException(400, "Missing admin header")
    return {"revoked": store().revoke(key_id)}
