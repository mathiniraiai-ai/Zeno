#!/usr/bin/env python3
"""Test the transcription service, locally or once deployed. Run from the repo root.

    python service/test_service.py                       # starts the service here and tests it (uses your .env Gemini key once)
    python service/test_service.py --no-live             # skip the real Gemini call
    python service/test_service.py --url https://<domain> --token zeno_...   # test a deployed service

Checks: health, missing/wrong key (401), non-WAV (400), oversize (413), per-person rate limit (429),
and a real transcription through the repo's own client (GeminiTranscriber with no local key).
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def env_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    env_file = ROOT / ".env"
    if not key and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    return key


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_wav(mb: float) -> bytes:
    return b"RIFF" + (b"\0" * int(mb * 1024 * 1024))


def live_transcription(url: str, token: str, audio: Path):
    """Use the repo's real client, as a team PC would: no local Gemini key, only the service address and personal key."""
    sys.path.insert(0, str(ROOT / "scripts"))
    os.environ["SCRIPTCUT_CLOUD_URL"], os.environ["SCRIPTCUT_CLOUD_TOKEN"] = url, token
    from align_engine.gemini_transcriber import GeminiTranscriber

    sys.path.insert(0, str(ROOT / "scripts"))
    from common import read_script_text
    from learn_reference import dialogue
    script = ROOT / "input" / "script" / "micronarrative_ref_ml.txt"
    lines = [l["text"] for l in dialogue(read_script_text(script))]
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.time()
        res = GeminiTranscriber(api_keys=[]).transcribe(str(audio), language_hint="ml", target_script_lines=lines, work_dir=Path(tmp), use_cache=False)
    words = res.get("words") or []
    latin = sum(1 for w in words if any(c.isascii() and c.isalpha() for c in w))
    native = sum(1 for w in words if any("ഀ" <= c <= "ൿ" for c in w))
    check("transcription through the repo client", res.get("status") == "success" and len(words) > 150 and not res.get("warning"),
          f"{len(words)} words ({latin} English, {native} Malayalam), model {res.get('model_used')}, {time.time() - t0:.0f}s")
    check("English and native words both present", latin > 10 and native > 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--token")
    ap.add_argument("--audio", default=str(ROOT / "work" / "micronarrative_ref" / "audio16k.wav"))
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    audio = Path(a.audio)

    proc = None
    if a.url:
        url, token = a.url.rstrip("/"), a.token
        if not token:
            sys.exit("--token is required with --url")
    else:
        sys.path.insert(0, str(SERVICE))
        tokens = {}
        for who in ("TestA", "TestB"):
            raw = "zeno_test_" + hashlib.sha256((who + str(time.time())).encode()).hexdigest()[:24]
            tokens[who] = raw
        port = free_port()
        env = {**os.environ, "GEMINI_API_KEY": env_key(), "ZENO_MAX_UPLOAD_MB": "5", "ZENO_RATE_PER_MIN": "6",
               "ZENO_API_TOKENS": ",".join(f"{n}:{hashlib.sha256(t.encode()).hexdigest()}" for n, t in tokens.items())}
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                                cwd=str(SERVICE), env=env)
        url, token = f"http://127.0.0.1:{port}", tokens["TestA"]
        for _ in range(60):
            try:
                if httpx.get(url + "/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)

    try:
        print(f"Testing {url}")
        h = httpx.get(url + "/health", timeout=10).json()
        check("health", h.get("ok") is True, str({k: v for k, v in h.items() if k != 'ok'}))
        post = lambda tok, data=b"RIFFxxxx", **kw: httpx.post(url + "/v1/transcribe", headers={"Authorization": f"Bearer {tok}"} if tok else {},
                                                             files={"file": ("a.wav", data, "audio/wav")}, data=kw or {}, timeout=30)
        check("no key -> 401", post(None).status_code == 401)
        check("wrong key -> 401", post("zeno_wrong").status_code == 401)
        if not a.url:
            check("not a WAV -> 400", post(token, data=b"hello world").status_code == 400)
            check("oversize -> 413", post(token, data=fake_wav(6)).status_code == 413)
        if not a.no_live:
            if not audio.is_file():
                check("test audio exists", False, str(audio))
            else:
                live_transcription(url, token, audio)
        if not a.url:
            other = {"TestB": tokens["TestB"]}["TestB"]
            codes = [post(other).status_code for _ in range(8)]
            check("rate limit -> 429 after 6 requests", 429 in codes and codes.index(429) == 6, str(codes))
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
