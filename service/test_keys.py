#!/usr/bin/env python3
"""Test the key signup, admin and key-check flow without calling Gemini. Run from the repo root.

    .venv/bin/python service/test_keys.py                       # SQLite temp file
    .venv/bin/python service/test_keys.py --database-url postgresql://...   # a real Postgres
"""
import argparse
import base64
import os
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--database-url", default="")
a = ap.parse_args()

tmp = tempfile.mkdtemp()
os.environ.update({"ZENO_SIGNUP_CODE": "team-code", "ZENO_ADMIN_PASSWORD": "admin-pw", "ZENO_DB_PATH": str(Path(tmp) / "k.db"),
                   "ZENO_API_TOKENS": "", "ZENO_RATE_PER_MIN": "100"})
if a.database_url:
    os.environ["DATABASE_URL"] = a.database_url
else:
    os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import app as service  # noqa: E402

client = TestClient(service.app)
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


basic = lambda pw: {"Authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}
signup = lambda **kw: client.post("/api/keys", json={"name": "Asha", "email": "asha@gosupernova.live", "code": "team-code", **kw})
ping = lambda key: client.post("/v1/transcribe", headers={"Authorization": f"Bearer {key}"}, files={"file": ("a.wav", b"not a wav", "audio/wav")})

print(f"Key store: {service.store().backend.split(':')[0]}")
check("signup page loads", client.get("/").status_code == 200 and "Generate" in client.get("/").text)
check("wrong team code -> 403", signup(code="nope").status_code == 403)
check("bad name -> 400", signup(name="<script>").status_code == 400)
check("non-company email -> 400", signup(email="asha@gmail.com").status_code == 400)
check("look-alike domain -> 400", signup(email="asha@gosupernova.live.evil.com").status_code == 400)
check("look-alike prefix -> 400", signup(email="asha@evilgosupernova.live").status_code == 400)

r = signup()
key = r.json().get("key", "")
check("signup returns a key once", r.status_code == 200 and key.startswith("zeno_") and r.headers.get("cache-control") == "no-store")
check("new key is accepted (reaches the WAV check, 400 not 401)", ping(key).status_code == 400)
check("unknown key -> 401", ping("zeno_unknown").status_code == 401)
rows = service.store().list()
check("only a hash is stored, never the key", len(rows) == 1 and key not in str(rows) and key not in str(service.store()._run("SELECT key_hash FROM zeno_keys", (), "all")))

check("admin without password -> 401", client.get("/admin").status_code == 401)
check("admin wrong password -> 401", client.get("/admin", headers=basic("bad")).status_code == 401)
check("admin page loads", client.get("/admin", headers=basic("admin-pw")).status_code == 200)
listing = client.get("/admin/api/keys", headers=basic("admin-pw")).json()["keys"]
check("admin sees the person, usage fields present", len(listing) == 1 and listing[0]["email"] == "asha@gosupernova.live" and listing[0]["requests"] == 0)

service.store().record_use(listing[0]["id"], 93.0)
row = client.get("/admin/api/keys", headers=basic("admin-pw")).json()["keys"][0]
check("usage is recorded", row["requests"] == 1 and abs(row["audio_seconds"] - 93.0) < 0.01 and row["last_used_at"])

signup(); signup()
check("4th active key for one email -> 409", signup().status_code == 409)

kid = listing[0]["id"]
check("revoke without admin header -> 400", client.post(f"/admin/api/keys/{kid}/revoke", headers=basic("admin-pw")).status_code == 400)
check("revoke without password -> 401", client.post(f"/admin/api/keys/{kid}/revoke", headers={"X-Zeno-Admin": "1"}).status_code == 401)
check("revoke works", client.post(f"/admin/api/keys/{kid}/revoke", headers={**basic("admin-pw"), "X-Zeno-Admin": "1"}).json() == {"revoked": True})
check("revoked key stops working at once -> 401", ping(key).status_code == 401)

for _ in range(6):
    last = signup(code="x").status_code
check("wrong-code attempts are rate limited -> 429", last == 429)
os.environ.pop("ZENO_SIGNUP_CODE")
service._signup_hits.clear()
check("with no team code set, name-only signup is refused -> 403", client.post("/api/keys", json={"name": "Ravi", "email": "Ravi@GoSupernova.live"}).status_code == 403)
check("health says no code required", client.get("/health").json()["signup_code_required"] is False)
os.environ["ZENO_SIGNUP_CODE"] = "team-code"

r2 = client.post("/api/keys", json={"name": "Mathinirai Selvan", "code": "team-code"})
check("name only -> email built as first.last@gosupernova.live", r2.status_code == 200 and r2.json()["email"] == "mathinirai.selvan@gosupernova.live", str(r2.json()))
r3 = client.post("/api/keys", json={"name": "Mathinirai@GoSupernova.live", "code": "team-code"})
check("typing the full address also works", r3.status_code == 200 and r3.json()["email"] == "mathinirai@gosupernova.live", str(r3.json()))
tel = client.post("/v1/telemetry", headers={"Authorization": "Bearer " + r3.json()["key"]}, json={
    "project": "test_ad", "language": "ml", "agent_name": "Antigravity", "agent_model": "Gemini 4 Argon",
    "clips_count": 2, "video_seconds": 24.5, "transcribe_in_tokens": 820, "transcribe_out_tokens": 190, "transcribe_total_tokens": 1010,
    "agent_in_tokens": 4200, "agent_cached_tokens": 12000, "agent_out_tokens": 950, "agent_total_tokens": 5150,
    "grand_total_tokens": 6160, "clips": [{"clip": "S1", "input_tokens": 410, "output_tokens": 95, "total_tokens": 505}],
})
check("telemetry endpoint stores run in DB", tel.status_code == 200 and tel.json().get("ok") is True, str(tel.json()))
admin_data = client.get("/admin/api/keys", headers=basic("admin-pw")).json()
check("admin API returns stored runs", len(admin_data.get("runs") or []) == 1 and admin_data["runs"][0]["grand_total_tokens"] == 6160)
h = client.get("/health").json()
check("health reports store and admin switch", h["key_store_ok"] and h["admin_enabled"], str(h))

# Google sign-in: ID token only, our own client id, company domain only (default gosupernova.live), no admin by domain
from unittest.mock import MagicMock, patch  # noqa: E402

os.environ["ZENO_GOOGLE_CLIENT_ID"] = "client-123.apps.googleusercontent.com"


def google_reply(**info):
    base = {"aud": "client-123.apps.googleusercontent.com", "email": "editor@gosupernova.live", "email_verified": "true", "name": "Live Editor", "sub": "123"}
    base.update(info)
    fake = MagicMock()
    fake.__enter__.return_value.get.return_value = MagicMock(status_code=200, json=lambda: base)
    return patch("httpx.Client", return_value=fake)


def google_login(**info):
    with google_reply(**info):
        return client.post("/api/auth/google", json={"credential": "fake.jwt.token"})


g_ok = google_login()
check("Google OAuth @gosupernova.live issues session key", g_ok.status_code == 200 and g_ok.json().get("key", "").startswith("zeno_"))
check("Google token for another app (wrong audience) -> 401", google_login(aud="someone-elses-client").status_code == 401)
check("Google account outside the company domain -> 403", google_login(email="x@gmail.com").status_code == 403)
check("@gosupernova.line is no longer allowed by default -> 403", google_login(email="editor@gosupernova.line").status_code == 403)
check("unverified Google email -> 401", google_login(email_verified="false").status_code == 401)
with patch.dict(os.environ, {"ZENO_ALLOWED_EMAIL_DOMAINS": "gosupernova.live,partner.example"}):
    check("extra domain works when listed in ZENO_ALLOWED_EMAIL_DOMAINS", google_login(email="a@partner.example").status_code == 200)
with google_reply():
    check("access_token alone is not accepted -> 400", client.post("/api/auth/google", json={"access_token": "ya29.x"}).status_code == 400)
with patch.dict(os.environ, {"ZENO_GOOGLE_CLIENT_ID": ""}):
    check("Google sign-in off without a client id -> 503", client.post("/api/auth/google", json={"credential": "fake.jwt.token"}).status_code == 503)
check("a company key is not admin by default -> 403", client.get("/admin/api/keys", headers={"Authorization": "Bearer " + g_ok.json()["key"]}).status_code == 403)
with patch.dict(os.environ, {"ZENO_ADMIN_EMAILS": "editor@gosupernova.live"}):
    check("an email listed in ZENO_ADMIN_EMAILS is admin", client.get("/admin/api/keys", headers={"Authorization": "Bearer " + g_ok.json()["key"]}).status_code == 200)

g_key = g_ok.json().get("key", "")
r_rule = client.post("/v1/learnings/rule", headers={"Authorization": f"Bearer {g_key}"}, json={
    "rule": "Keep split_h static z=1.0 with common headroom",
    "category": "split_screen",
    "language": "kn",
})
check("POST /v1/learnings/rule stores shared rule", r_rule.status_code == 200 and r_rule.json().get("ok") is True)
r_lrn = client.get("/v1/learnings", headers={"Authorization": f"Bearer {g_key}"})
check("GET /v1/learnings returns learned_rules & language_patterns", r_lrn.status_code == 200 and len(r_lrn.json().get("learned_rules") or []) >= 1)

# Pre-job self-test: a small WAV plus a Gemini ping; needs a valid key
import io as _io, wave as _wave  # noqa: E402

_buf = _io.BytesIO()
with _wave.open(_buf, "wb") as _w:
    _w.setnchannels(1); _w.setsampwidth(2); _w.setframerate(16000); _w.writeframes(b"\x00\x00" * 16000)
_auth = {"Authorization": "Bearer " + g_ok.json()["key"]}
with patch.object(service, "_gemini_ping", return_value={"ok": True, "detail": "stub answered"}):
    st = client.post("/v1/selftest", headers=_auth, files={"file": ("probe.wav", _buf.getvalue(), "audio/wav")})
    check("selftest passes with a small WAV", st.status_code == 200 and st.json()["ok"] is True and any(c["name"] == "audio upload accepted" and c["ok"] for c in st.json()["checks"]), str(st.json()))
    bad = client.post("/v1/selftest", headers=_auth, files={"file": ("probe.wav", b"not audio", "audio/wav")})
    check("selftest fails when the audio cannot be read", bad.status_code == 200 and bad.json()["ok"] is False)
check("selftest needs a key -> 401", client.post("/v1/selftest").status_code == 401)
with patch.object(service, "_gemini_ping", return_value={"ok": False, "detail": "no Gemini key is configured on the server"}):
    check("selftest reports a Gemini problem", client.post("/v1/selftest", headers=_auth).json()["ok"] is False)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
