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
                    busy = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                    time.sleep(min(4.0, 1.0 + attempt + random.uniform(0.1, 0.5)) if busy else 0.3)
        if best is not None:
            best["warning"] = f"incomplete: {count(best)} words for {duration:.0f}s of audio"
            return best
        raise HTTPException(502, "Transcription failed upstream")


_gemini = _Gemini()


def _allowed_domains() -> List[str]:
    raw = os.getenv(
        "ZENO_ALLOWED_EMAIL_DOMAINS",
        os.getenv("ZENO_ALLOWED_EMAIL_DOMAIN", "gosupernova.live,gosupernova.line"),
    )
    domains = [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]
    for default_dom in ("gosupernova.live", "gosupernova.line"):
        if default_dom not in domains and raw.strip() in ("", "gosupernova.live", "gosupernova.line"):
            domains.append(default_dom)
    return domains or ["gosupernova.live", "gosupernova.line"]


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
        "admin_allow_domain": os.getenv("ZENO_ADMIN_ALLOW_DOMAIN", "1").strip().lower() in ("1", "true", "yes"),
    }


@app.get("/v1/me")
def get_me(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    name, key_id = authenticate(authorization)
    email = store().get_key_email(key_id) if key_id is not None and hasattr(store(), "get_key_email") else ""
    return {"ok": True, "name": name, "email": email, "key_id": key_id}


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
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    tot_tok = int(usage.get("total_tokens") or (in_tok + out_tok))
    if key_id is not None:
        try:
            store().record_use(key_id, duration, input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok)
        except Exception:
            log.exception("could not record usage")
    log.info("user=%s bytes=%d status=200 model=%s words=%d in_tok=%d out_tok=%d seconds=%.1f%s", name, len(audio), result.get("model_used"), len(result.get("words") or []), in_tok, out_tok, time.time() - started, " WARNING=" + result["warning"] if result.get("warning") else "")
    return result


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


class SignupBody(BaseModel):
    name: str
    email: str = ""   # optional: when empty it is built from the name
    code: str = ""


class GoogleAuthBody(BaseModel):
    credential: str = ""      # Google Identity Services ID token (JWT)
    access_token: str = ""    # OAuth2 access token
    code: str = ""            # OAuth2 authorization code
    redirect_uri: str = ""    # Used when exchanging authorization code
    name: str = ""


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
    """Verify Google ID token / access token / auth code via Google's tokeninfo endpoint using httpx."""
    import httpx

    id_token = body.credential.strip()
    access_token = body.access_token.strip()
    client_id = (os.getenv("ZENO_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("ZENO_GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()

    with httpx.Client(timeout=10.0) as client:
        if body.code.strip() and not id_token and not access_token:
            if not client_id or not client_secret:
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
            tok_data = token_resp.json()
            id_token = tok_data.get("id_token", "")
            access_token = tok_data.get("access_token", "")

        if id_token:
            resp = client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
            if resp.status_code != 200:
                raise HTTPException(401, "Invalid Google ID token")
            info = resp.json()
            if client_id and info.get("aud") != client_id:
                raise HTTPException(401, "Google ID token audience mismatch")
        elif access_token:
            resp = client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code != 200:
                raise HTTPException(401, "Invalid Google access token")
            info = resp.json()
        else:
            raise HTTPException(400, "Missing Google credential or access_token")

    email = str(info.get("email") or "").strip().lower()
    verified = str(info.get("email_verified", "true")).lower() in ("true", "1")
    if not email or not verified:
        raise HTTPException(401, "Google account email is not verified")

    if not _is_allowed_email(email):
        raise HTTPException(403, "Only @gosupernova.live / @gosupernova.line accounts are permitted")

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
    profile = _verify_google_identity(body)
    email = profile["email"]
    name = profile["name"]
    try:
        res = store().create_or_get_google_key(name, email, google_sub=profile.get("sub", ""))
    except Exception:
        log.exception("key store unavailable during google auth")
        raise HTTPException(503, "Could not issue key right now, try again shortly")

    log.info("google auth key issued for %s <%s>", name, email)
    return JSONResponse(
        {
            "key": res["key"],
            "token": res["key"],
            "name": name,
            "email": email,
            "prefix": res["prefix"],
            "key_id": res["key_id"],
            "picture": profile.get("picture", ""),
        },
        headers=NO_STORE,
    )


@app.post("/api/keys")
def create_key(body: SignupBody, request: Request) -> JSONResponse:
    code = os.getenv("ZENO_SIGNUP_CODE", "")
    ip = _client_ip(request)
    _check_signup_rate(ip)
    if code and not hmac.compare_digest(body.code.encode(), code.encode()):
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
    log.info("key created for %s <%s>", name, email)
    return JSONResponse({"key": key, "token": key, "name": name, "email": email, "prefix": key[:12]}, headers=NO_STORE)


def _check_admin(
    authorization: Optional[str] = None,
    x_admin_password: Optional[str] = None,
) -> Dict[str, str]:
    password = os.getenv("ZENO_ADMIN_PASSWORD", "").strip()
    allow_domain = os.getenv("ZENO_ADMIN_ALLOW_DOMAIN", "1").strip().lower() in ("1", "true", "yes")
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
