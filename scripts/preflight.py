#!/usr/bin/env python3
"""Pre-job gate: prove the machine and the server work, then check the script and footage the user handed over.

    python3 scripts/preflight.py                         # live system checks, saved to work/preflight.json
    python3 scripts/preflight.py --inputs <clips> --script <script.txt>   # also check the script and footage

`zeno.py --inspect` runs this itself and refuses to start a job unless it passes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ROOT = common.ROOT
STATE_FILE = common.WORK / "preflight.json"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
Check = Tuple[Optional[bool], str, str]   # (ok / None = not applicable, label, detail)


# ----------------------------------------------------------------------------- system checks
def check_ffmpeg() -> Check:
    ff = common.resolve_ffmpeg()
    graph = "tpad=stop=2:stop_mode=clone,lutrgb=r='val+(255-val)*100/255':enable='between(t,0,0.2)',format=yuv420p"
    r = subprocess.run([str(ff), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.5:r=24", "-vf", graph, "-f", "null", "-"],
                       capture_output=True, text=True)
    l = subprocess.run([str(ff), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-f", "null", "-"],
                       capture_output=True, text=True)
    ok = r.returncode == 0 and l.returncode == 0
    return ok, "FFmpeg", "filters and loudness normalisation run" if ok else (r.stderr or l.stderr).strip()[:200]


def check_opencv() -> Check:
    """Decode a real frame and run the YuNet face detector on it (not just 'the package imports')."""
    try:
        import cv2
        import numpy as np

        frame = np.full((240, 320, 3), 90, dtype=np.uint8)
        ok_enc, buf = cv2.imencode(".png", frame)
        back = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok_enc else None
        if back is None or back.shape != frame.shape:
            return False, "OpenCV", "could not encode and decode a test frame"
        if not common.YUNET_MODEL.is_file():
            return False, "OpenCV", f"face model missing: {common.YUNET_MODEL.name}"
        det = cv2.FaceDetectorYN.create(str(common.YUNET_MODEL), "", (320, 240))
        det.detect(frame)
        return True, "OpenCV", f"cv2 {cv2.__version__}: frame decode and {common.YUNET_MODEL.name} detector run"
    except Exception as exc:   # noqa: BLE001
        return False, "OpenCV", f"{type(exc).__name__}: {str(exc)[:160]}"


def check_aligner() -> Check:
    """Load wav2vec2 and run one forward pass on 1 s of audio."""
    code = (
        "import sys, os, tempfile, time\n"
        f"sys.path.insert(0, r'{Path(__file__).resolve().parent}')\n"
        "import numpy as np, soundfile as sf\n"
        "from align_engine.acoustic import AcousticModel\n"
        "t = time.time(); m = AcousticModel()\n"
        "p = os.path.join(tempfile.gettempdir(), 'zeno_preflight.wav'); sf.write(p, np.zeros(16000, dtype='float32'), 16000)\n"
        "lp, _, dur, tpf = m.extract_emissions(p)\n"
        "print('OK device=%s frames=%d seconds=%.1f' % (m.device, lp.shape[0], time.time() - t))\n"
    )
    r = subprocess.run([str(common.find_venv_python()), "-c", code], capture_output=True, text=True, timeout=300)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0 and line.startswith("OK")
    return ok, "wav2vec2 aligner", line[3:] if ok else (r.stderr.strip()[-200:] or "no output")


def _small_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """Low-level noise so the file is a real WAV of a real length; nothing is transcribed from it."""
    import random

    rng = random.Random(7)
    frames = b"".join(int(rng.uniform(-300, 300)).to_bytes(2, "little", signed=True) for _ in range(int(seconds * rate)))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    data = Path(path).read_bytes()
    os.unlink(path)
    return data


def _ping_local_gemini_keys() -> Optional[Tuple[bool, str]]:
    """If local GEMINI_API_KEY(s) are set in .env, run a live ping so owner PCs work when cloud Gemini quota is exhausted."""
    try:
        from align_engine.gemini_transcriber import get_gemini_api_keys
        from google import genai
        from google.genai import types

        keys = get_gemini_api_keys()
        if not keys:
            return None
        models = [
            "gemini-3.1-flash-lite",
            "gemini-3-flash-preview",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-flash-lite-latest",
        ]
        for model in models:
            t0 = time.time()
            for key in keys:
                for _retry in range(2):
                    try:
                        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60000))
                        reply = client.models.generate_content(
                            model=model,
                            contents="Reply with the single word: ok",
                            config=types.GenerateContentConfig(
                                temperature=0.0,
                                thinking_config=types.ThinkingConfig(thinking_budget=0),
                            ),
                        )
                        if (reply.text or "").strip():
                            return True, f"{model} answered in {time.time() - t0:.1f}s (local .env GEMINI_API_KEY)"
                    except Exception:
                        continue
        return False, "local .env GEMINI_API_KEY failed on every model"
    except Exception as exc:   # noqa: BLE001
        return False, f"local .env GEMINI_API_KEY check failed ({type(exc).__name__})"


def check_server() -> List[Check]:
    """Send a small WAV to the Railway service and have it check its own dependencies. Falls back to /health + /v1/me
    when the server is older than /v1/selftest, and says so."""
    import auth_session
    import httpx

    common.load_dotenv()
    info = auth_session.get_session_info()
    if info.get("mode") == "gemini_direct":
        local_ping = _ping_local_gemini_keys()
        if local_ping is not None:
            return [(local_ping[0], "Gemini direct (.env)", local_ping[1])]
        return [(None, "Zeno server", "not used: this PC has its own Gemini key (not called)")]
    token, base = auth_session.get_active_token(), auth_session.get_cloud_url().rstrip("/")
    if not token:
        return [(False, "Zeno server", "not signed in. Run: python3 scripts/setup.py --login")]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = httpx.post(base + "/v1/selftest", headers=headers, files={"file": ("probe.wav", _small_wav(), "audio/wav")}, timeout=40.0)
        if r.status_code == 404:   # older server
            h = httpx.get(base + "/health", timeout=15.0).json()
            me = httpx.get(base + "/v1/me", headers=headers, timeout=15.0)
            return [
                (me.status_code == 200, "Zeno server: key accepted", "signed in" if me.status_code == 200 else f"server answered {me.status_code}: sign in again"),
                (bool(h.get("gemini_configured")) and bool(h.get("key_store_ok")), "Zeno server: dependencies (basic)",
                 "Gemini key and key store are up (the server has no /v1/selftest yet, so no audio probe was possible)"),
            ]
        if r.status_code == 401:
            return [(False, "Zeno server: key accepted", "the server rejected your key. Run: python3 scripts/setup.py --login")]
        if r.status_code != 200:
            return [(False, "Zeno server", f"self-test answered {r.status_code}")]
        out: List[Check] = []
        for c in r.json().get("checks", []):
            ok = bool(c.get("ok"))
            name = str(c.get("name", ""))
            detail = str(c.get("detail", ""))
            if not ok and name == "Gemini reachable":
                local_ping = _ping_local_gemini_keys()
                if local_ping is not None and local_ping[0]:
                    ok, detail = True, local_ping[1]
            out.append((ok, f"Zeno server: {name}", detail))
        return out
    except Exception as exc:   # noqa: BLE001
        return [(False, "Zeno server", f"cannot reach {base}: {type(exc).__name__}")]


def check_gpu_acceleration() -> Check:
    """Ensure NVIDIA Windows/Linux PCs use CUDA PyTorch + NVENC FFmpeg, and report active GPU aligner + video encoder."""
    if not common.IS_MAC and os.environ.get("ZENO_DEVICE", "auto").strip().lower() != "cpu":
        try:
            import gpu_install

            if gpu_install.detect_tag():
                py = str(common.find_venv_python())
                if not gpu_install.is_cuda_ready(py):
                    print("  [GPU] NVIDIA GPU detected with CPU PyTorch — auto-installing CUDA PyTorch...", flush=True)
                    print("  [GPU] " + gpu_install.ensure_cuda_torch(), flush=True)
                msg_ff = gpu_install.ensure_nvenc_ffmpeg()
                if msg_ff and "Installed" in msg_ff:
                    print("  [GPU] " + msg_ff, flush=True)
        except Exception:
            pass
    rep = common.gpu_report()
    dev = str(rep.get("device") or "cpu").upper()
    enc = str(rep.get("encoder") or "libx264")
    gpu_name = f" ({rep['name']})" if rep.get("name") else ""
    detail = f"aligner={dev}{gpu_name}, encoder={enc}"
    if rep.get("hint"):
        detail += f" — {rep['hint']}"
    return True, "GPU acceleration", detail


def run_system_checks() -> List[Check]:
    checks: List[Check] = [check_ffmpeg(), check_gpu_acceleration(), check_opencv(), check_aligner()]
    checks.extend(check_server())
    return checks


# ----------------------------------------------------------------------------- state
def save_state(checks: List[Check]) -> bool:
    passed = all(ok is not False for ok, _, _ in checks)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"passed": passed, "at": time.time(), "checks": [{"ok": o, "label": l, "detail": d} for o, l, d in checks]}, indent=1), encoding="utf-8")
    return passed


def fresh_pass(max_age_hours: float = 6.0) -> bool:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(st.get("passed")) and (time.time() - float(st.get("at", 0))) < max_age_hours * 3600


# ----------------------------------------------------------------------------- script and footage
def collect_footage(inputs: List[str]) -> Tuple[List[Path], List[str]]:
    files: List[Path] = []
    problems: List[str] = []
    for raw in inputs:
        if raw.lower().startswith(("http://", "https://")):
            problems.append(f"{raw[:60]}... is a link. Download the footage folder first and give its local path (links are not downloaded automatically).")
            continue
        p = Path(raw).expanduser()
        if p.is_dir():
            found = sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."))
            if not found:
                problems.append(f"{p} has no video files ({', '.join(sorted(VIDEO_EXT))})")
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            problems.append(f"{raw} does not exist")
    return files, problems


def probe_clip(path: Path) -> Dict[str, Any]:
    r = subprocess.run([str(common.FFPROBE), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        data = {}
    kinds = [s.get("codec_type") for s in data.get("streams", [])]
    vid = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": float((data.get("format") or {}).get("duration") or 0), "video": "video" in kinds, "audio": "audio" in kinds,
            "size": f"{vid.get('width', '?')}x{vid.get('height', '?')}"}


def validate_inputs(script_path: Path, inputs: List[str]) -> Dict[str, Any]:
    """Check the script and footage the user handed over. `errors` stop the job; `warnings` are shown."""
    import prepare

    errors: List[str] = []
    warnings: List[str] = []
    report: Dict[str, Any] = {}

    try:
        meta, lines = prepare.parse_script_file(script_path)
    except Exception as exc:   # noqa: BLE001
        return {"errors": [f"the script could not be read: {type(exc).__name__}: {exc}"], "warnings": [], "report": {}}
    spoken = [l for l in lines if not l.get("beat")]
    beats = [l for l in lines if l.get("beat")]
    speakers = sorted({l["speaker"] for l in spoken if l.get("speaker")})
    report["script"] = {
        "spoken_lines": len(spoken), "silent_beats": len(beats), "scenes": len({l["scene"] for l in lines}), "speakers": speakers,
        "strike_text_lines": sum(1 for l in lines if l.get("strike_text")), "layout_notes": sorted({l["layout_hint"] for l in lines if l.get("layout_hint")}),
    }
    if not spoken:
        errors.append("the script has no spoken lines")
    for l in spoken:
        if len(l.get("tokens", [])) > 60:
            warnings.append(f"{l['id']} is {len(l['tokens'])} words long; one clip rarely holds that much")
    if spoken and not speakers:
        warnings.append("no speaker names found; wrong-speaker checks will be skipped")

    files, problems = collect_footage(inputs)
    errors.extend(problems)
    clips = []
    total = 0.0
    for f in files:
        pr = probe_clip(f)
        total += pr["duration"]
        if not pr["video"] or pr["duration"] < 0.3:
            errors.append(f"{f.name} is not a readable video")
        elif not pr["audio"]:
            warnings.append(f"{f.name} has no audio (fine only for silent beats)")
        clips.append({"name": f.name, **pr})
    report["footage"] = {"clips": len(clips), "total_seconds": round(total, 1)}
    if files and len(files) < len(spoken) / 4:
        warnings.append(f"{len(files)} clips for {len(spoken)} spoken lines; some lines may have no clip")
    return {"errors": errors, "warnings": warnings, "report": report, "clips": clips}


def print_inputs_report(res: Dict[str, Any]) -> None:
    rep = res.get("report", {})
    s, f = rep.get("script"), rep.get("footage")
    if s:
        print(f"  Script : {s['spoken_lines']} spoken lines, {s['silent_beats']} silent beats, {s['scenes']} scenes, speakers: {', '.join(s['speakers']) or 'none named'}")
        extras = []
        if s["strike_text_lines"]:
            extras.append(f"{s['strike_text_lines']} strike-through text lines")
        if s["layout_notes"]:
            extras.append("layout notes: " + ", ".join(s["layout_notes"]))
        if extras:
            print("           " + "; ".join(extras))
    if f:
        print(f"  Footage: {f['clips']} clips, {f['total_seconds']} s in total")
    for w in res.get("warnings", []):
        print(f"  [WARN] {w}")
    for e in res.get("errors", []):
        print(f"  [FAIL] {e}")


def print_checks(checks: List[Check]) -> None:
    for ok, label, detail in checks:
        mark = "N/A " if ok is None else ("PASS" if ok else "FAIL")
        print(f"  [{mark}] {label:<38} {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Live pre-job checks, then optional script and footage validation")
    ap.add_argument("--inputs", nargs="*", help="footage folder(s) or clip files")
    ap.add_argument("--script", help="local script file")
    a = ap.parse_args()
    print("\nZeno pre-job checks (live)")
    checks = run_system_checks()
    print_checks(checks)
    passed = save_state(checks)
    print("\nSYSTEM: " + ("ALL CHECKS PASSED" if passed else "NOT READY. Fix the FAIL lines above, then run this again."))
    if passed and a.inputs and a.script:
        res = validate_inputs(Path(a.script), a.inputs)
        print("\nYour inputs")
        print_inputs_report(res)
        passed = passed and not res["errors"]
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
