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
# A revoked key must stay revoked across restarts: starting the store must never un-revoke anyone
import re as _re  # noqa: E402
_src = (Path(__file__).resolve().parent / "keystore.py").read_text(encoding="utf-8")
check("the key store never un-revokes a key at startup", not _re.search(r"revoked_at\s*=\s*NULL", _src))
_reopened = service.KeyStore(sqlite_path=os.environ["ZENO_DB_PATH"]) if hasattr(service, "KeyStore") else None
check("a revoked key is still revoked after the store is reopened", _reopened is not None and _reopened.lookup(key) is None)

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
usage = client.get("/api/usage", headers=basic("admin-pw"))
check("usage log needs a sign-in", client.get("/api/usage").status_code == 401)
ur = (usage.json().get("runs") or [{}])[0] if usage.status_code == 200 else {}
check("usage log returns the run's numbers", usage.status_code == 200 and ur.get("agent_in_tokens") == 4200 and ur.get("agent_out_tokens") == 950
      and ur.get("agent_model") == "Gemini 4 Argon" and ur.get("transcribe_total_tokens") == 1010, str(ur)[:160])
check("usage log carries no plan files or key hashes", usage.status_code == 200 and "final_edl" not in ur and "initial_edl" not in ur
      and all("key_hash" not in k for k in usage.json().get("keys", [])))
company_key = r3.json()["key"]
check("any company key can read the usage log (no admin list)", client.get("/api/usage", headers={"Authorization": "Bearer " + company_key}).status_code == 200)
check("...but that key is still not an admin", client.get("/admin/api/keys", headers={"Authorization": "Bearer " + company_key}).status_code == 403)
check("a made-up key cannot read the usage log", client.get("/api/usage", headers={"Authorization": "Bearer zeno_notakey"}).status_code in (401, 403))
page = client.get("/usage")
check("the usage page itself holds no data and needs no sign-in to open", page.status_code == 200 and "Who is using what" in page.text and "test_ad" not in page.text and "@gosupernova.live" not in page.text.replace("@gosupernova.live\"", ""))
check("the sign-in page has the Admin button top right", 'href="/usage"' in client.get("/").text)
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

with google_reply():
    check("a company Google account can read the usage log", client.get("/api/usage", headers={"Authorization": "Bearer fake.jwt.token"}).status_code == 200)
with google_reply(email="x@gmail.com"):
    check("a Google account outside the company cannot -> 403", client.get("/api/usage", headers={"Authorization": "Bearer fake.jwt.token"}).status_code == 403)

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

# Transcript cache: the same audio, prompt and script lines never reach Gemini twice
_calls = []


def _fake_gemini(audio, prompt, duration, trace=None, language="auto"):
    _calls.append(1)
    return {"words": ["hello", "world"], "model_used": "stub", "usage": {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}}


def transcribe(lang="ml", lines="[]", wav=None):
    return client.post("/v1/transcribe", headers=_auth, files={"file": ("a.wav", wav or _buf.getvalue(), "audio/wav")},
                       data={"language_hint": lang, "target_script_lines": lines})


with patch.object(service._gemini, "transcribe", side_effect=_fake_gemini):
    first, second = transcribe(), transcribe()
    check("transcript cache: second identical request is served without Gemini", len(_calls) == 1 and second.json().get("cached") is True and second.json()["usage"]["total_tokens"] == 0, str(second.json()))
    check("transcript cache: first answer is untouched", first.status_code == 200 and not first.json().get("cached"))
    transcribe(lang="kn")
    transcribe(lines='["a different script line"]')
    check("transcript cache: another language or script line is a new request", len(_calls) == 3)
_calls.clear()


def _warn_gemini(audio, prompt, duration, trace=None, language="auto"):
    _calls.append(1)
    return {"words": ["x"], "warning": "incomplete", "model_used": "stub"}


_other = _io.BytesIO()
with _wave.open(_other, "wb") as _w2:
    _w2.setnchannels(1); _w2.setsampwidth(2); _w2.setframerate(16000); _w2.writeframes(b"\x01\x00" * 16000)
with patch.object(service._gemini, "transcribe", side_effect=_warn_gemini):
    transcribe(wav=_other.getvalue()); transcribe(wav=_other.getvalue())
    check("transcript cache: an incomplete transcript is never cached", len(_calls) == 2)

# Transcription monitoring: every call is recorded with its result, model and the reason a model failed
def _dead_gemini(audio, prompt, duration, trace=None, language="auto"):
    if trace is not None:
        trace.append("gemini-3.5-flash-lite: timed out at Google (504)")
        trace.append("gemini-2.5-flash: timed out at Google (504)")
    raise service.HTTPException(502, "Transcription failed upstream")


def _fallback_gemini(audio, prompt, duration, trace=None, language="auto"):
    if trace is not None:
        trace.append("gemini-3.5-flash-lite: timed out at Google (504)")
    return {"words": ["hello", "world"], "model_used": "gemini-2.5-flash", "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}


_third = _io.BytesIO()
with _wave.open(_third, "wb") as _w3:
    _w3.setnchannels(1); _w3.setsampwidth(2); _w3.setframerate(16000); _w3.writeframes(b"\x02\x00" * 24000)
_fourth = _io.BytesIO()
with _wave.open(_fourth, "wb") as _w4:
    _w4.setnchannels(1); _w4.setsampwidth(2); _w4.setframerate(16000); _w4.writeframes(b"\x03\x00" * 24000)
with patch.object(service._gemini, "transcribe", side_effect=_dead_gemini):
    failed_call = transcribe(wav=_third.getvalue())
with patch.object(service._gemini, "transcribe", side_effect=_fallback_gemini):
    transcribe(wav=_fourth.getvalue())
events = service.store().transcription_events()
by_status = {e["status"]: e for e in events}
check("monitoring: a failed call is recorded with its reason", failed_call.status_code == 502 and by_status.get("failed", {}).get("reason") == "timed out at Google (504)")
check("monitoring: the failed models are listed", "gemini-2.5-flash" in by_status.get("failed", {}).get("failed_models", ""))
check("monitoring: a call saved by a fallback model is recorded as ok with the model that answered",
      any(e["status"] == "ok" and e["model"] == "gemini-2.5-flash" and "504" in e["failed_models"] for e in events))
check("monitoring: an incomplete transcript is recorded as incomplete", any(e["status"] == "incomplete" for e in events))
check("script check: Bengali letters in a Malayalam clip are caught", service._foreign_script_words(["ഹലോ", "নমস্কার", "hello"], "ml") == ["নমস্কার"])
check("script check: English and the clip's own script pass", service._foreign_script_words(["ഹലോ", "hello", "OK,", "ശരി."], "malayalam") == [])
check("script check: unknown or auto language is never judged", service._foreign_script_words(["নমস্কার"], "auto") == [] and service._foreign_script_words(["নমস্কার"], "xx") == [])
check("script check: Hindi in Devanagari passes", service._foreign_script_words(["नमस्ते", "hello"], "hi") == [])
check("monitoring: events carry no audio or transcript text", all(set(e) <= set(service.store()._TX_COLS) for e in events))
check("monitoring: the usage API returns the events", client.get("/api/usage", headers=basic("admin-pw")).json().get("transcriptions"))

# Per-job format observations -> candidates -> approval -> approved recipes
def observe(script_hash, verified=True, seconds=2.5, fmt="split_h"):
    return client.post("/v1/observations", headers=_auth, json={"style": "micronarrative", "script_hash": script_hash, "verified": verified, "video_seconds": 60,
                       "formats": [{"format": fmt, "segments": 4, "seconds": seconds * 4, "mean_shot_sec": seconds}]})


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import format_learning  # noqa: E402

_real = format_learning.build_observation({"segments": [{"id": "L1", "start": 0, "end": 3, "layout": "split_h"}], "marks": []}, "# style: micronarrative\n", "script", True)
check("what the client builds is accepted by the server", client.post("/v1/observations", headers=_auth, json=_real).status_code == 200, str(_real))
check("observation needs a key -> 401", client.post("/v1/observations", json={}).status_code in (401, 422))
check("a bad script hash is refused -> 400", observe("not hex!").status_code == 400)
for i, sec in enumerate([2.0, 2.2, 2.4, 2.6]):
    observe(f"{i + 1:08x}", seconds=sec)
observe("00000001", seconds=9.0)            # the same script again counts once
observe("000000aa", verified=False)         # unverified jobs are ignored
_admin = {**basic("admin-pw"), "X-Zeno-Admin": "1"}
agg = client.get("/admin/api/formats", headers=basic("admin-pw")).json()
row = next(r for r in agg["formats"] if r["format"] == "split_h")
check("candidates count distinct verified scripts only", row["n_jobs"] == 5, str(row))
check("candidate stats are medians; a re-rendered script is counted once, newest render", row["stats"]["mean_shot_sec"]["median"] == 2.6 and row["stats"]["mean_shot_sec"]["p25"] == 2.4, str(row["stats"]))
with patch.object(service, "MIN_FORMAT_JOBS", 6):
    check("approval needs enough jobs -> 409", client.post("/admin/api/formats/micronarrative/split_h/approve", headers=_admin).status_code == 409)
check("approval works at the minimum number of jobs", client.post("/admin/api/formats/micronarrative/split_h/approve", headers=_admin).status_code == 200)
check("approval needs the admin header -> 400", client.post("/admin/api/formats/micronarrative/split_h/approve", headers=basic("admin-pw")).status_code == 400)
approved = client.get("/v1/formats", headers=_auth).json()["formats"]
check("approved recipes are served to computers", len(approved) == 1 and approved[0]["format"] == "split_h" and approved[0]["n_jobs"] == 5, str(approved))
check("a company key cannot approve -> 403", client.post("/admin/api/formats/micronarrative/split_h/approve", headers={**_auth, "X-Zeno-Admin": "1"}).status_code == 403)

# Knowledge in the database: published once by an admin, pulled by every computer, matched on the server
def _fake_embed(text):   # a tiny stand-in for Gemini: a word-count vector over a few topic words
    vocab = ["split", "screen", "video", "call", "floating", "selfie", "top", "bottom", "left", "right", "chat", "bar"]
    t = text.lower()
    return [float(t.count(w)) for w in vocab] + [0.01]


import knowledge_sync  # noqa: E402

_recipes = knowledge_sync.collect_local()
check("local knowledge is collected (formats and references)", sum(1 for i in _recipes if i["kind"] == "format") >= 6 and any(i["kind"] == "reference" for i in _recipes))
_adm = {**basic("admin-pw"), "X-Zeno-Admin": "1"}
with patch.object(service, "_embed", side_effect=_fake_embed):
    for it in _recipes:
        r = client.put(f"/admin/api/knowledge/{it['kind']}/{it['name']}", headers=_adm, json={"payload": it["payload"], "text": it["text"]})
        assert r.status_code == 200, (it["name"], r.text)
    check("an admin publishes knowledge without a deploy", True)
    check("publishing needs an admin -> 403", client.put("/admin/api/knowledge/format/x", headers={**_auth, "X-Zeno-Admin": "1"}, json={"payload": {}}).status_code == 403)
    check("publishing needs the admin header -> 400", client.put("/admin/api/knowledge/format/x", headers=basic("admin-pw"), json={"payload": {}}).status_code == 400)
    check("an unknown kind is refused -> 400", client.put("/admin/api/knowledge/script/x", headers=_adm, json={"payload": {}}).status_code == 400)
    check("a path trick in the name is refused -> 400", client.put("/admin/api/knowledge/format/..%2Fx", headers=_adm, json={"payload": {}}).status_code in (400, 404))
    pulled = client.get("/v1/knowledge", headers=_auth).json()
    check("a computer pulls everything published", len(pulled["items"]) == len(_recipes), str(len(pulled["items"])))
    again = client.get("/v1/knowledge", params={"since": pulled["now"]}, headers=_auth).json()
    check("a second pull gets only what is new (nothing)", again["items"] == [])
    client.put("/admin/api/knowledge/format/split_v", headers=_adm, json={"payload": {"id": "split_v", "layout": "split_v", "description": "updated"}, "text": "updated split"})
    changed = client.get("/v1/knowledge", params={"since": pulled["now"]}, headers=_auth).json()["items"]
    check("an update reaches computers on their next pull", [i["name"] for i in changed] == ["split_v"] and changed[0]["payload"]["description"] == "updated")
    m = client.post("/v1/match", headers=_auth, json={"text": "a floating window video call"}).json()["matches"]
    check("match by embedding picks the video call recipe", m[0]["name"] == "video_call" and m[0]["method"] == "embedding", str(m))
# one item published while embedding was down is filled in at match time
_calls_emb = []
with patch.object(service, "_embed", return_value=None):
    client.put("/admin/api/knowledge/format/late_item", headers=_adm, json={"payload": {"id": "late_item", "layout": "single", "names": ["late wording"]}, "text": "late wording"})
with patch.object(service, "_embed", side_effect=_fake_embed):
    client.post("/v1/match", headers=_auth, json={"text": "floating video call"})
    check("a missing embedding is filled in when matching", service.store().knowledge_for_match("format") and all(i["embedding"] for i in service.store().knowledge_for_match("format")))
with patch.object(service, "_embed", return_value=None):
    m = client.post("/v1/match", headers=_auth, json={"text": "left screen and right screen"}).json()["matches"]
    check("match falls back to words when the server cannot embed", m[0]["name"] == "split_h" and m[0]["method"] == "words", str(m))
check("match needs a key -> 401", client.post("/v1/match", json={"text": "x"}).status_code == 401)
check("match refuses an empty question -> 400", client.post("/v1/match", headers=_auth, json={"text": " "}).status_code == 400)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
