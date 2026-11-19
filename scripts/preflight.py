#!/usr/bin/env python3
"""Pre-job readiness gate for Zeno (`scripts/preflight.py`).

Verifies FFmpeg/FFprobe, GPU compute & H.264 encoder, OpenCV + YuNet face detector,
PyTorch/torchaudio wav2vec2 aligner, Node.js subtitle renderer, and transcription credentials,
plus optional footage & script validation.

Usage:
    python3 scripts/preflight.py
    python3 scripts/preflight.py --inputs <clips_or_dir...> --script <script_path_or_gdoc_url>
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import argparse
import json
import random
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_session  # noqa: E402
import common  # noqa: E402
from common import DEFAULT_LOUDNESS_LUFS  # noqa: E402
import script_intake  # noqa: E402

ROOT = common.ROOT
STATE_FILE = common.WORK / "preflight.json"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
Check = Tuple[Optional[bool], str, str]


# ----------------------------------------------------------------------------- System Checks
def check_ffmpeg() -> Check:
    """Verify FFmpeg video filtergraph + loudnorm audio filter and FFprobe availability."""
    ff = common.resolve_ffmpeg()
    fp = common.resolve_ffprobe()
    if not ff.is_file() or not fp.is_file():
        return False, "FFmpeg & FFprobe", f"missing binary (ffmpeg={ff.is_file()}, ffprobe={fp.is_file()})"

    graph = "tpad=stop=2:stop_mode=clone,lutrgb=r='val+(255-val)*100/255':enable='between(t,0,0.2)',format=yuv420p"
    r = subprocess.run(
        [
            str(ff),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=0.5:r=24",
            "-vf",
            graph,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    l = subprocess.run(
        [
            str(ff),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=1",
            "-af",
            f"loudnorm=I={DEFAULT_LOUDNESS_LUFS:.1f}:TP=-1.0:LRA=11",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    ok = r.returncode == 0 and l.returncode == 0
    return (
        ok,
        "FFmpeg & FFprobe",
        f"video filters, ffprobe, and {DEFAULT_LOUDNESS_LUFS:.1f} LUFS loudnorm ready" if ok else (r.stderr or l.stderr).strip()[:200],
    )


def check_gpu_acceleration() -> Check:
    """Report active ML compute device and verified hardware H.264 encoder."""
    rep = common.gpu_report()
    dev = str(rep.get("device") or "cpu").upper()
    enc = str(rep.get("encoder") or "libx264")
    gpu_name = f" ({rep['name']})" if rep.get("name") else ""
    detail = f"aligner={dev}{gpu_name}, encoder={enc}"
    if rep.get("hint"):
        detail += f" — {rep['hint']}"
    return True, "GPU acceleration", detail


def check_opencv() -> Check:
    """Decode a real test frame and run the YuNet ONNX face detector on it."""
    try:
        import cv2
        import numpy as np

        frame = np.full((240, 320, 3), 90, dtype=np.uint8)
        ok_enc, buf = cv2.imencode(".png", frame)
        back = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok_enc else None
        if back is None or back.shape != frame.shape:
            return False, "OpenCV & YuNet", "could not encode and decode a test frame"
        if not common.YUNET_MODEL.is_file():
            return False, "OpenCV & YuNet", f"face model missing: {common.YUNET_MODEL.name}"
        det = cv2.FaceDetectorYN.create(str(common.YUNET_MODEL), "", (320, 240))
        det.detect(frame)
        return True, "OpenCV & YuNet", f"cv2 {cv2.__version__}: frame decode and {common.YUNET_MODEL.name} ready"
    except Exception as exc:
        return False, "OpenCV & YuNet", f"{type(exc).__name__}: {str(exc)[:160]}"


def check_pytorch_audio() -> Check:
    """Verify PyTorch, torchaudio, soundfile, and wav2vec2 acoustic alignment forward pass."""
    scripts_dir = Path(__file__).resolve().parent
    torch_home = common.MODELS_DIR / "torch"
    code = (
        "import importlib.util, os, sys, tempfile, time\n"
        "sys.dont_write_bytecode = True\n"
        f"os.environ.setdefault('TORCH_HOME', r'{torch_home}')\n"
        "os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')\n"
        f"sys.path.insert(0, r'{scripts_dir}')\n"
        "import numpy as np, soundfile as sf, torch, torchaudio\n"
        "import common\n"
        "t = time.time()\n"
        "acoustic_py = os.path.join(r'" + str(scripts_dir) + "', 'align_engine', 'acoustic.py')\n"
        "AcousticModel = None\n"
        "if os.path.isfile(acoustic_py):\n"
        "    try:\n"
        "        from align_engine.acoustic import AcousticModel\n"
        "    except Exception:\n"
        "        spec = importlib.util.spec_from_file_location('_zeno_acoustic', acoustic_py)\n"
        "        mod = importlib.util.module_from_spec(spec)\n"
        "        spec.loader.exec_module(mod)\n"
        "        AcousticModel = mod.AcousticModel\n"
        "if AcousticModel is not None:\n"
        "    m = AcousticModel()\n"
        "    fd, p = tempfile.mkstemp(suffix='.wav'); os.close(fd)\n"
        "    try:\n"
        "        sf.write(p, np.zeros(16000, dtype='float32'), 16000)\n"
        "        lp, _, _, _ = m.extract_emissions(p)\n"
        "    finally:\n"
        "        if os.path.exists(p): os.unlink(p)\n"
        "    print('OK device=%s frames=%d seconds=%.1f' % (m.device, lp.shape[0], time.time() - t))\n"
        "else:\n"
        "    from torchaudio.pipelines import WAV2VEC2_ASR_BASE_960H as bundle\n"
        "    dev = common.compute_device()\n"
        "    model = bundle.get_model().to(dev).eval()\n"
        "    with torch.inference_mode():\n"
        "        em, _ = model(torch.zeros((1, 16000), dtype=torch.float32, device=dev))\n"
        "    print('OK torch=%s torchaudio=%s device=%s frames=%d seconds=%.1f' % (\n"
        "        torch.__version__, torchaudio.__version__, dev, em.shape[1], time.time() - t))\n"
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        r = subprocess.run(
            [str(common.find_venv_python()), "-B", "-c", code],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        line = (r.stdout.strip().splitlines() or [""])[-1]
        ok = r.returncode == 0 and line.startswith("OK ")
        return ok, "PyTorch & wav2vec2", line[3:] if ok else (r.stderr.strip()[-200:] or "aligner check produced no output")
    except Exception as exc:
        return False, "PyTorch & wav2vec2", f"{type(exc).__name__}: {str(exc)[:160]}"


check_aligner = check_pytorch_audio


def check_subtitle_renderer() -> Check:
    """Verify Node.js, `node-canvas`, bundled subtitle styles & fonts, and `subtitles/caption-renderer.js`."""
    node = common.resolve_node()
    if node is None or not node.is_file():
        return False, "Node subtitle renderer", "Node.js binary not found on PATH or tools/node"

    sub_dir = common.SUBTITLES_DIR
    required = ("caption-renderer.js", "render_cues.js", "styles.json", "fonts.json")
    missing = [name for name in required if not (sub_dir / name).is_file()]
    if missing:
        return False, "Node subtitle renderer", f"missing files in subtitles/: {', '.join(missing)}"

    js_probe = (
        "const fs = require('fs');"
        "const { createCanvas } = require('canvas');"
        "const { CaptionRenderer } = require('./caption-renderer.js');"
        "const styles = JSON.parse(fs.readFileSync('./styles.json', 'utf8'));"
        "const c = createCanvas(320, 120);"
        "const ctx = c.getContext('2d');"
        "ctx.fillStyle = '#fff'; ctx.fillText('ok', 10, 40);"
        "const buf = c.toBuffer('image/png');"
        "if (!buf || buf.length < 50 || !CaptionRenderer || !Array.isArray(styles) || !styles.length) process.exit(2);"
        "console.log('OK node=' + process.version + ' styles=' + styles.length);"
    )
    try:
        r = subprocess.run(
            [str(node), "-e", js_probe],
            cwd=str(sub_dir),
            capture_output=True,
            text=True,
            timeout=20,
        )
        line = (r.stdout.strip().splitlines() or [""])[-1]
        ok = r.returncode == 0 and line.startswith("OK ")
        return (
            ok,
            "Node subtitle renderer",
            f"{line[3:]}, node-canvas PNG render verified" if ok else (r.stderr.strip()[-200:] or "node-canvas check failed"),
        )
    except Exception as exc:
        return False, "Node subtitle renderer", f"{type(exc).__name__}: {str(exc)[:160]}"


def _small_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """Generate a 1-second low-level noise WAV probe payload."""
    rng = random.Random(7)
    frames = b"".join(int(rng.uniform(-300, 300)).to_bytes(2, "little", signed=True) for _ in range(int(seconds * rate)))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(frames)
        return Path(path).read_bytes()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _collect_local_gemini_keys() -> List[str]:
    common.load_dotenv()
    try:
        from align_engine.gemini_transcriber import get_gemini_api_keys  # type: ignore

        keys = list(get_gemini_api_keys() or [])
        if keys:
            return keys
    except Exception:
        pass

    seen: List[str] = []
    for k in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GOOGLE_API_KEY",
    ):
        val = os.environ.get(k, "").strip()
        if val and not val.lower().startswith("your_") and val not in seen:
            seen.append(val)
    return seen


def _ping_local_gemini_keys() -> Optional[Tuple[bool, str]]:
    """Verify local `GEMINI_API_KEY` connectivity if configured."""
    keys = _collect_local_gemini_keys()
    if not keys:
        return None
    if os.environ.get("ZENO_NO_LIVE") == "1" or os.environ.get("CLAUDE_EDITOR_OFFLINE") == "1":
        return True, f"{len(keys)} Gemini API key(s) configured (offline check)"
    try:
        from google import genai
        from google.genai import types

        models = [
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
        ]
        for model in models:
            t0 = time.time()
            for key in keys:
                try:
                    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=20000))
                    reply = client.models.generate_content(
                        model=model,
                        contents="Reply with the single word: ok",
                        config=types.GenerateContentConfig(temperature=0.0),
                    )
                    if (reply.text or "").strip():
                        return True, f"{model} responded in {time.time() - t0:.1f}s (direct Gemini API key)"
                except Exception:
                    continue
        return False, "configured GEMINI_API_KEY failed live connectivity check"
    except Exception as exc:
        return False, f"Gemini client check failed ({type(exc).__name__})"


def _login_command() -> str:
    """Return the working cross-platform Zeno Cloud sign-in command."""
    if common.IS_WIN:
        py = r".venv\Scripts\python.exe" if (ROOT / ".venv" / "Scripts" / "python.exe").is_file() else "python"
    else:
        py = ".venv/bin/python" if (ROOT / ".venv" / "bin" / "python").is_file() else "python3"
    return f"{py} scripts/setup.py --login"


def check_transcription_auth() -> List[Check]:
    """Verify Zeno Cloud session or direct Gemini API key readiness (never printing secret values)."""
    common.load_dotenv()
    info = auth_session.get_session_info()
    offline = os.environ.get("ZENO_NO_LIVE") == "1" or os.environ.get("CLAUDE_EDITOR_OFFLINE") == "1"

    if not info.get("authenticated"):
        return [(False, "Transcription auth", f"not signed in and no GEMINI_API_KEY found. Run: {_login_command()}")]

    if offline:
        return [(True, "Transcription auth", f"configured via {info.get('source')} ({info.get('mode')}, offline mode)")]

    if info.get("mode") == "gemini_direct":
        local_ping = _ping_local_gemini_keys()
        if local_ping is not None:
            return [(local_ping[0], "Transcription auth (Gemini)", local_ping[1])]
        return [(True, "Transcription auth (Gemini)", f"API key configured via {info.get('source')}")]

    token = auth_session.get_active_token()
    base = auth_session.get_cloud_url().rstrip("/")
    headers = auth_session.get_auth_headers()
    try:
        import httpx

        r = httpx.post(
            base + "/v1/selftest",
            headers=headers,
            files={"file": ("probe.wav", _small_wav(), "audio/wav")},
            timeout=35.0,
        )
        if r.status_code == 404:
            h = httpx.get(base + "/health", timeout=15.0).json()
            me = httpx.get(base + "/v1/me", headers=headers, timeout=15.0)
            if me.status_code == 200:
                return [
                    (True, "Zeno Cloud: session", f"signed in ({info.get('source')})"),
                    (
                        bool(h.get("gemini_configured", True)),
                        "Zeno Cloud: transcription",
                        "cloud service healthy",
                    ),
                ]
            local_ping = _ping_local_gemini_keys()
            if local_ping is not None and local_ping[0]:
                return [(True, "Transcription auth (Gemini fallback)", local_ping[1])]
            return [(False, "Zeno Cloud: session", f"server returned HTTP {me.status_code}; run: {_login_command()}")]

        if r.status_code == 401:
            local_ping = _ping_local_gemini_keys()
            if local_ping is not None and local_ping[0]:
                return [(True, "Transcription auth (Gemini fallback)", local_ping[1])]
            return [(False, "Zeno Cloud: session", f"session token rejected; run: {_login_command()}")]

        if r.status_code != 200:
            local_ping = _ping_local_gemini_keys()
            if local_ping is not None and local_ping[0]:
                return [(True, "Transcription auth (Gemini fallback)", local_ping[1])]
            return [(False, "Zeno Cloud", f"self-test returned HTTP {r.status_code}")]

        out: List[Check] = []
        for c in r.json().get("checks", []):
            ok = bool(c.get("ok"))
            name = str(c.get("name", "check"))
            detail = str(c.get("detail", ""))
            if not ok and "Gemini" in name:
                local_ping = _ping_local_gemini_keys()
                if local_ping is not None and local_ping[0]:
                    ok, detail = True, local_ping[1]
            out.append((ok, f"Zeno Cloud: {name}", detail))
        return out or [(True, "Zeno Cloud", f"authenticated ({bool(token)})")]
    except Exception as exc:
        local_ping = _ping_local_gemini_keys()
        if local_ping is not None and local_ping[0]:
            return [(True, "Transcription auth (Gemini fallback)", local_ping[1])]
        return [(False, "Zeno Cloud", f"cannot reach {base}: {type(exc).__name__}")]


check_server = check_transcription_auth


def run_system_checks() -> List[Check]:
    """Run all live preflight checks in deterministic order."""
    checks: List[Check] = [
        check_ffmpeg(),
        check_gpu_acceleration(),
        check_opencv(),
        check_pytorch_audio(),
        check_subtitle_renderer(),
    ]
    checks.extend(check_transcription_auth())
    return checks


# ----------------------------------------------------------------------------- State Persistence
def save_state(checks: List[Check]) -> bool:
    """Persist preflight status to `work/preflight.json` and return True if all checks passed."""
    passed = all(ok is not False for ok, _, _ in checks)
    common.write_json(
        STATE_FILE,
        {
            "passed": passed,
            "at": time.time(),
            "checks": [{"ok": o, "label": l, "detail": d} for o, l, d in checks],
        },
    )
    return passed


def fresh_pass(max_age_hours: float = 6.0) -> bool:
    """Return True if `work/preflight.json` passed within the last `max_age_hours`."""
    st = common.read_json(STATE_FILE, default=None)
    if not isinstance(st, dict):
        return False
    return bool(st.get("passed")) and (time.time() - float(st.get("at", 0))) < max_age_hours * 3600.0


# ----------------------------------------------------------------------------- Script & Footage Validation
def collect_footage(inputs: List[str]) -> Tuple[List[Path], List[str]]:
    """Resolve input clip files or footage folders, returning `(video_files, error_messages)`."""
    files: List[Path] = []
    problems: List[str] = []
    for raw in inputs:
        if raw.lower().startswith(("http://", "https://")):
            problems.append(
                f"{raw[:60]}... is a remote URL. Download the footage locally first and pass its folder path."
            )
            continue
        p = Path(raw).expanduser()
        if not p.exists():
            p = common.resolve(raw)
        if p.is_dir():
            found = sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."))
            if not found:
                problems.append(f"{p} contains no video files ({', '.join(sorted(VIDEO_EXT))})")
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            problems.append(f"{raw} does not exist")
    return files, problems


def probe_clip(path: Path) -> Dict[str, Any]:
    """Probe a single clip for duration, video/audio stream presence, and pixel dimensions."""
    data = common.ffprobe_json(path)
    kinds = [s.get("codec_type") for s in data.get("streams", [])]
    vid = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    return {
        "duration": dur,
        "video": "video" in kinds,
        "audio": "audio" in kinds,
        "size": f"{vid.get('width', '?')}x{vid.get('height', '?')}",
    }


def _fallback_parse_script_file(script_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Lightweight fallback script parser used if `prepare.py` is not yet importable."""
    text = common.read_script_text(script_path)
    lines: List[Dict[str, Any]] = []
    scene = 1
    idx = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^(?:(?:scene|act)\s+\d+|scene\s+chang(?:e|es|ed)\b)", s, flags=re.I):
            scene += 1
            continue
        if s.startswith("[") and s.endswith("]"):
            idx += 1
            lines.append({"id": f"B{idx}", "beat": True, "speaker": "", "text": s[1:-1].strip(), "tokens": [], "scene": scene})
            continue
        m = re.match(r"^(?:(L\d+)\s+)?([A-Za-z][A-Za-z0-9 _.-]{0,28}):\s+(.+)$", s)
        idx += 1
        if m:
            lid = m.group(1) or f"L{idx}"
            speaker = m.group(2).strip()
            spoken_text = m.group(3).strip()
        else:
            lid = f"L{idx}"
            speaker = ""
            spoken_text = s
        tokens = [tok for tok in re.split(r"\s+", spoken_text) if tok]
        lines.append({
            "id": lid,
            "beat": False,
            "speaker": speaker,
            "text": spoken_text,
            "tokens": tokens,
            "scene": scene,
            "strike_text": "~~" in spoken_text,
            "layout_hint": "",
        })
    return {}, lines


def validate_inputs(script_path: Path | str, inputs: List[str]) -> Dict[str, Any]:
    """Validate script and footage inputs before launching an edit (`errors` block; `warnings` inform)."""
    errors: List[str] = []
    warnings: List[str] = []
    report: Dict[str, Any] = {}

    try:
        resolved_script = (
            script_intake.resolve_script_input(str(script_path))
            if script_intake.is_google_doc_url(str(script_path))
            else common.resolve(script_path)
        )
        if not resolved_script.is_file():
            return {"errors": [f"Script file not found: {script_path}"], "warnings": [], "report": {}, "clips": []}
        try:
            import prepare  # type: ignore

            _meta, lines = prepare.parse_script_file(resolved_script)
        except ImportError:
            _meta, lines = _fallback_parse_script_file(resolved_script)
    except Exception as exc:
        return {"errors": [f"the script could not be read: {type(exc).__name__}: {exc}"], "warnings": [], "report": {}, "clips": []}

    spoken = [ln for ln in lines if not ln.get("beat")]
    beats = [ln for ln in lines if ln.get("beat")]
    speakers = sorted({str(ln["speaker"]) for ln in spoken if ln.get("speaker")})
    report["script"] = {
        "spoken_lines": len(spoken),
        "silent_beats": len(beats),
        "scenes": len({ln.get("scene", 1) for ln in lines}) if lines else 0,
        "speakers": speakers,
        "strike_text_lines": sum(1 for ln in lines if ln.get("strike_text")),
        "layout_notes": sorted({str(ln["layout_hint"]) for ln in lines if ln.get("layout_hint")}),
    }
    if not spoken:
        errors.append("the script has no spoken lines")
    for ln in spoken:
        toks = ln.get("tokens") or []
        if len(toks) > 60:
            warnings.append(f"{ln.get('id', 'line')} is {len(toks)} words long; one clip rarely holds that much")
    if spoken and not speakers:
        warnings.append("no speaker names found; speaker-consistency checks will be skipped")

    files, problems = collect_footage(inputs)
    errors.extend(problems)
    clips: List[Dict[str, Any]] = []
    total = 0.0
    for f in files:
        pr = probe_clip(f)
        total += pr["duration"]
        if not pr["video"] or pr["duration"] < 0.3:
            errors.append(f"{f.name} is not a readable video clip")
        elif not pr["audio"]:
            warnings.append(f"{f.name} has no audio stream (usable only for silent visual beats)")
        clips.append({"name": f.name, **pr})
    report["footage"] = {"clips": len(clips), "total_seconds": round(total, 1)}
    if files and spoken and len(files) < len(spoken) / 4:
        warnings.append(f"{len(files)} clips for {len(spoken)} spoken lines; verify all scenes are included")
    return {"errors": errors, "warnings": warnings, "report": report, "clips": clips}


def print_inputs_report(res: Dict[str, Any]) -> None:
    rep = res.get("report", {})
    s, f = rep.get("script"), rep.get("footage")
    if s:
        print(
            f"  Script : {s['spoken_lines']} spoken lines, {s['silent_beats']} silent beats, "
            f"{s['scenes']} scenes, speakers: {', '.join(s['speakers']) or 'none named'}"
        )
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
    ap = argparse.ArgumentParser(description="Zeno live pre-job readiness checks & input validation")
    ap.add_argument("--inputs", nargs="*", help="Footage folder(s) or clip files")
    ap.add_argument("--script", help="Local script path or Google Docs tab link")
    a = ap.parse_args()

    print("\nZeno pre-job checks")
    checks = run_system_checks()
    print_checks(checks)
    passed = save_state(checks)
    print("\nSYSTEM: " + ("ALL CHECKS PASSED" if passed else "NOT READY — resolve the FAIL items above before editing."))
    if passed and a.inputs and a.script:
        res = validate_inputs(a.script, a.inputs)
        print("\nInputs")
        print_inputs_report(res)
        passed = passed and not res["errors"]
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
