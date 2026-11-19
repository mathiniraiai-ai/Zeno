#!/usr/bin/env python3
"""Zeno — Unified Orchestrator (`scripts/zeno.py`).

Modes:
  1. Host-AI Direct Step-by-Step Mode:
       .venv/bin/python scripts/zeno.py --inspect "<footage>" --script "<script>" --name "<Name>" [--compact]
       .venv/bin/python scripts/zeno.py --check-plan work/<project>/plan.edl
       .venv/bin/python scripts/zeno.py --execute work/<project>/plan.edl --square --compact
  2. Multi-Video Sequential Queue Mode:
       .venv/bin/python scripts/zeno.py --queue work/queue.json [--inspect-only] [--compact]
  3. Readiness, Preflight & Revision Feedback:
       .venv/bin/python scripts/zeno.py --preflight [--compact]
       .venv/bin/python scripts/zeno.py --check
       .venv/bin/python scripts/zeno.py --feedback "<note>" [--plan work/<project>/plan.edl]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT_BOOT = Path(__file__).resolve().parent.parent
if str(ROOT_BOOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_BOOT / "scripts"))

import common  # noqa: E402
import edl_parser  # noqa: E402
from edl_parser import load_edl  # noqa: E402
from progress import ProgressTracker  # noqa: E402

_VENV_PY = common.find_venv_python()
if (
    _VENV_PY
    and os.path.exists(_VENV_PY)
    and os.path.realpath(sys.executable) != os.path.realpath(str(_VENV_PY))
    and not os.environ.get("_ZENO_REEXEC")
):
    os.environ["_ZENO_REEXEC"] = "1"
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

if __name__ == "__main__":
    common.run_cool()

ROOT = common.ROOT
WORK = common.WORK
OUTPUT = common.OUTPUT
VIDEO_EXTS = (".mp4", ".MP4", ".mov", ".MOV", ".m4v", ".M4V", ".mkv", ".MKV", ".webm", ".WEBM")
GDOC_RE = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
COMPACT_Child_RE = re.compile(
    r"^\[(?:BLOCKED|ORDER BLOCKED|PLAN PASS|VERIFY|VOICE|AUDIO|CUTS|SCRIPT|FRAMES|PICTURE|SPLIT|MARKS|SUBS|SQUARE|PACKET|PASS|FAIL|WARN|WARNING|ERROR)",
    re.I,
)


def _venv_py_cmd() -> str:
    return r".venv\Scripts\python.exe" if common.IS_WIN else ".venv/bin/python"


def _rel_workspace_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _pretty_path(path: Path) -> str:
    p = Path(path).expanduser()
    try:
        rel_home = p.resolve().relative_to(Path.home().resolve()).as_posix()
        return f"~/{rel_home}" + ("/" if p.is_dir() else "")
    except Exception:
        return str(p) + ("/" if p.is_dir() else "")


def _delivery_base_dir() -> Path:
    base_fn = getattr(common, "_delivery_base_dir", None)
    if callable(base_fn):
        try:
            return Path(base_fn())
        except Exception:
            pass
    return Path.home() / "Downloads" / "Zeno Outs"


def _target_delivery_dir(vname: str) -> Path:
    return _delivery_base_dir() / common.job_folder_name(vname)


def _target_delivery_display(vname: str) -> str:
    base = _pretty_path(_delivery_base_dir()).rstrip("/")
    return f"{base}/{common.job_folder_name(vname)}/"


def _packet_path(proj_dir: Path) -> Path:
    editing = proj_dir / "editing_packet.md"
    if editing.is_file():
        return editing
    director = proj_dir / "director_packet.md"
    if director.is_file():
        return director
    return editing


def _maybe_auto_update() -> None:
    if os.environ.get("ZENO_NO_AUTO_UPDATE", "").strip() == "1":
        return
    try:
        import auto_updater

        auto_updater.ensure_latest_update(min_interval_hours=12.0, quiet=True)
    except Exception:
        pass


def _archive_call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    try:
        import job_archive

        fn = getattr(job_archive, fn_name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    except Exception:
        pass
    return None


def _job_start_from_project(proj_dir: Path, snap: Dict[str, Any]) -> float:
    cand = snap.get("job_start_epoch")
    try:
        if cand and float(cand) > 0:
            return float(cand)
    except (TypeError, ValueError):
        pass
    started_file = proj_dir / "job_started.json"
    if started_file.is_file():
        try:
            started = json.loads(started_file.read_text(encoding="utf-8")).get("start")
            if started and float(started) > 0:
                return float(started)
        except Exception:
            pass
    js_fn = getattr(common, "job_start_epoch", None)
    if callable(js_fn):
        try:
            val = js_fn(create=False)
            if val and float(val) > 0:
                return float(val)
        except Exception:
            pass
    return time.time() - 900.0


def _capture_token_session(proj_dir: Path, completed_at: Optional[float] = None) -> None:
    """Fail-open agent token session capture into work/<project>/token_telemetry.json."""
    try:
        import token_tracker

        if not hasattr(token_tracker, "detect_agent_usage"):
            return
        snap_path = proj_dir / "token_telemetry.json"
        raw_snap = common.read_json(snap_path) if snap_path.is_file() else {}
        snap: Dict[str, Any] = dict(raw_snap) if isinstance(raw_snap, dict) else {}
        job_start = _job_start_from_project(proj_dir, snap)
        now = float(completed_at if completed_at is not None else time.time())
        codex_sid = snap.get("agent_session_id") if str(snap.get("agent_name", "")).startswith("Codex") else None
        agent = token_tracker.detect_agent_usage(since_epoch=job_start, until_epoch=now, session_id=codex_sid)
        if not isinstance(agent, dict):
            return
        a_in = int(agent.get("input_tokens") or 0)
        a_cache = int(agent.get("cached_tokens") or 0)
        a_out = int(agent.get("output_tokens") or 0)
        a_tot = int(agent.get("total_tokens") or (a_in + a_out))
        snap.setdefault("project", proj_dir.name)
        snap.setdefault("job_start_epoch", job_start)
        snap["job_end_epoch"] = now
        if a_tot > 0 or a_cache > 0 or not snap.get("agent_total_tokens"):
            snap["agent_name"] = agent.get("agent_name") or snap.get("agent_name") or "unknown"
            snap["agent_model"] = agent.get("agent_model") or snap.get("agent_model") or "unknown"
            snap["agent_session_id"] = agent.get("session_id") or snap.get("agent_session_id")
            snap["agent_token_precision"] = agent.get("token_precision") or snap.get("agent_token_precision") or "unavailable"
            snap["agent_steps"] = int(agent.get("steps_count") or 0)
            snap["agent_context_start"] = int(agent.get("context_start") or 0)
            snap["agent_context_end"] = int(agent.get("context_end") or 0)
            snap["agent_in_tokens"] = a_in
            snap["agent_cached_tokens"] = a_cache
            snap["agent_out_tokens"] = a_out
            snap["agent_total_tokens"] = a_tot
            snap["agent_total_including_cache"] = a_tot + a_cache
            snap["host_usage_unknown"] = bool(agent.get("host_usage_unknown", False))
        proj_dir.mkdir(parents=True, exist_ok=True)
        common.write_json(snap_path, snap)
    except Exception:
        pass


def _resolve_output_mp4(plan_path: Path, plan: Dict[str, Any], folder: Optional[Path] = None) -> Tuple[Path, Path]:
    snap_path = plan_path.parent / "token_telemetry.json"
    snap = common.read_json(snap_path) if snap_path.is_file() else {}
    snap_out = snap.get("output_mp4") if isinstance(snap, dict) else None
    raw_out = snap_out or plan.get("output") or (OUTPUT / f"{plan_path.parent.name}.mp4")
    telemetry_out = Path(str(raw_out))
    if not snap_out and not telemetry_out.is_absolute():
        telemetry_out = ROOT / telemetry_out
    archive_out = telemetry_out if telemetry_out.is_absolute() else (ROOT / telemetry_out)
    if not archive_out.with_suffix(".timeline.txt").is_file() and folder is not None and folder.is_dir():
        for cand in sorted(folder.glob("*.mp4")):
            if cand.with_suffix(".timeline.txt").is_file():
                archive_out = cand
                break
    return telemetry_out, archive_out


def _finalize_execute_telemetry(plan_path: Path, plan: Dict[str, Any], video_seconds: float, telemetry_out: Path) -> None:
    """Fail-open job telemetry finalization on execute success, with session fallback."""
    try:
        import token_tracker

        if hasattr(token_tracker, "finalize_job_telemetry"):
            token_tracker.finalize_job_telemetry(
                plan_path=plan_path,
                plan=plan,
                video_seconds=video_seconds,
                output_mp4=telemetry_out,
                sync_cloud=True,
                completed_at=time.time(),
            )
            return
    except Exception:
        pass
    _capture_token_session(plan_path.parent)


def _infer_video_name(plan: Dict[str, Any], plan_path: Path, explicit_name: str = "") -> str:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()
    out_raw = str(plan.get("output") or "").strip()
    if out_raw:
        out_p = Path(out_raw)
        if out_p.parent.name and out_p.parent.resolve() != OUTPUT.resolve():
            return common.strip_ratio_prefix(out_p.parent.name)
        if out_p.stem:
            return common.strip_ratio_prefix(out_p.stem)
    return common.strip_ratio_prefix(plan_path.parent.name)


# ============================================================================
# Readiness & Pre-Flight Gates
# ============================================================================
def check_system_readiness(quiet: bool = False) -> Tuple[bool, List[Tuple[bool, str, str]]]:
    checks: List[Tuple[bool, str, str]] = []
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    has_venv = (ROOT / ".venv").exists()
    checks.append((True, "Python & Venv", f"Python {py_ver} ({'.venv active' if has_venv else 'system python'})"))

    ff_ok = bool(common.FFMPEG and common.FFPROBE and os.path.exists(common.FFMPEG) and os.path.exists(common.FFPROBE))
    checks.append((ff_ok, "FFmpeg & FFprobe", str(common.FFMPEG) if ff_ok else "Missing ffmpeg/ffprobe binary"))

    missing_pkgs = []
    for mod_name, label in (("cv2", "opencv-python"), ("numpy", "numpy"), ("PIL", "Pillow"), ("scipy", "scipy")):
        try:
            __import__(mod_name)
        except ImportError:
            missing_pkgs.append(label)
    checks.append((not missing_pkgs, "Media & CV Packages", "opencv, numpy, Pillow, scipy ready" if not missing_pkgs else f"Missing: {', '.join(missing_pkgs)}"))

    yunet_ok = common.YUNET_MODEL.is_file() and common.YUNET_MODEL.stat().st_size > 100_000
    checks.append((yunet_ok, "YuNet Face Tracker", f"{common.YUNET_MODEL.name} ready" if yunet_ok else "Missing YuNet ONNX model"))

    has_auth = bool(
        os.path.exists(ROOT / ".zeno_session.json")
        or os.path.exists(ROOT / ".env")
        or os.environ.get("ZENO_CLOUD_TOKEN")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    checks.append((has_auth, "Transcription Auth", "Connected (.zeno_session.json / .env)" if has_auth else "Run: .venv/bin/python scripts/setup.py --login"))

    fonts_dir = common.FONTS_DIR
    ttf_cnt = len(list(fonts_dir.glob("*.ttf"))) if fonts_dir.is_dir() else 0
    checks.append((ttf_cnt >= 1, "Indic & UI Fonts", f"{ttf_cnt} bundled font(s) ready" if ttf_cnt else "Missing bundled fonts"))

    pr_shell = ROOT / "assets" / "premiere" / "base_1080x1920.prproj.gz"
    sfx_ok = (common.SFX_DIR / "whoosh.wav").is_file() and (common.MARKS_DIR / "red_flash.png").is_file() and pr_shell.is_file()
    checks.append((sfx_ok, "SFX, Marks & .prproj", "flash/vignette marks + SFX + Premiere shell ready" if sfx_ok else "Missing assets in assets/"))

    reg_ok = (ROOT / "formats" / "registry.json").is_file() and (ROOT / "formats" / "core_rules.md").is_file()
    checks.append((reg_ok, "Segregated Formats", "formats/registry.json + catalog cards ready" if reg_ok else "Missing formats/registry.json"))

    all_ok = all(ok for ok, _, _ in checks)
    if not quiet:
        print("╔══════════════════════════════════════════════════════════════════════╗")
        print("║                    ZENO EDIT BAY — READINESS CHECK                   ║")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        for ok, label, detail in checks:
            print(f"  [{'✓' if ok else '✗'}] {label:<22} : {detail}")
        print("────────────────────────────────────────────────────────────────────────")
        print(f"  STATUS: {'READY' if all_ok else 'ACTION NEEDED'} ({sum(1 for ok, _, _ in checks if ok)}/{len(checks)} checks passed)\n")
    return all_ok, checks


def resolve_script_input(script_arg: str) -> Path:
    s = (script_arg or "").strip()
    m = GDOC_RE.search(s)
    if not m:
        p = common.resolve(s)
        if not p.is_file():
            raise FileNotFoundError(f"Script file not found: {s}")
        return p

    import script_intake

    doc_id = m.group(1)
    info = script_intake.parse_doc_url(s)
    if not info.get("tab") and not info.get("heading"):
        raise ValueError("Google Doc URL has no ?tab=t.xxxx or #heading — share the specific script tab link.")
    try:
        text, _note = script_intake.fetch_google_doc(s)
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:
        raise RuntimeError(f"Could not download Google Doc ({doc_id}): {exc}") from exc
    if len(text.strip()) < 10:
        raise RuntimeError("The linked Google Doc tab is empty.")
    out_dir = ROOT / "input" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", info.get("tab") or info.get("heading") or "")[:24]
    dest = out_dir / f"gdoc_{doc_id[:12]}_{key}.txt"
    dest.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
    return dest


def expand_clip_inputs(raw_inputs: List[str]) -> List[Path]:
    clips: List[Path] = []
    seen = set()
    for raw in raw_inputs:
        p = Path(raw).expanduser()
        p = p.resolve() if p.exists() else common.resolve(raw)
        if p.is_dir():
            for ext in VIDEO_EXTS:
                for child in sorted(p.glob(f"*{ext}")):
                    if child.is_file() and not child.name.startswith(".") and str(child) not in seen:
                        seen.add(str(child))
                        clips.append(child)
        elif p.is_file() and str(p) not in seen:
            seen.add(str(p))
            clips.append(p)
    return clips


def run_system_preflight(skip: bool = False, quiet_if_fresh: bool = False) -> Tuple[bool, str]:
    import preflight

    if skip:
        return True, "skipped"
    if quiet_if_fresh and preflight.fresh_pass(max_age_hours=2.0):
        return True, "cached preflight pass"
    print("\n== Pre-job gate: live system checks ==", flush=True)
    checks = preflight.run_system_checks()
    preflight.print_checks(checks)
    if not preflight.save_state(checks):
        failed = "; ".join(f"{lbl}: {det}" for ok, lbl, det in checks if ok is False)
        return False, failed or "system preflight check failed"
    return True, "passed"


def validate_job_inputs(raw_inputs: List[str], script_arg: str, skip: bool = False) -> Tuple[Path, Optional[str]]:
    import preflight

    script_path = resolve_script_input(script_arg)
    if skip:
        return script_path, None
    res = preflight.validate_inputs(script_path, raw_inputs)
    preflight.print_inputs_report(res)
    if res.get("errors"):
        return script_path, "; ".join(str(e) for e in res["errors"])
    return script_path, None


# ============================================================================
# Subprocess Runner with Full Log Capture & 25s Keepalive Heartbeat
# ============================================================================
def _stream_subprocess(
    cmd: List[str],
    stage_label: str,
    compact: bool = False,
    on_line: Optional[Callable[[str], None]] = None,
) -> Tuple[int, List[str]]:
    log_dir = WORK / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{stage_label}-{uuid.uuid4().hex[:8]}.log"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("ZENO_MAX_THREADS", str(common.ZENO_MAX_THREADS))

    tail: "collections.deque[str]" = collections.deque(maxlen=40)
    started = time.monotonic()

    with log_file.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _pump() -> None:
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    q.put(raw)
            finally:
                q.put(None)

        t = threading.Thread(target=_pump, daemon=True)
        t.start()
        try:
            while True:
                try:
                    raw = q.get(timeout=25.0)
                except queue.Empty:
                    if proc.poll() is not None and q.empty():
                        break
                    elapsed = int(round(time.monotonic() - started))
                    print(f"[PROGRESS] {stage_label} running ({elapsed}s)...", flush=True)
                    continue
                if raw is None:
                    break
                lf.write(raw)
                lf.flush()
                line = raw.rstrip("\r\n")
                stripped = line.strip()
                if not stripped:
                    continue
                tail.append(stripped)
                if on_line:
                    on_line(stripped)
                if not compact:
                    print(f"  {line}", flush=True)
                elif COMPACT_Child_RE.match(stripped):
                    print(stripped, flush=True)
            t.join(timeout=2.0)
            rc = proc.wait()
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise

    if rc != 0 and compact and tail:
        print(f"[ERROR] {stage_label} exited with {rc}; tail:\n  " + "\n  ".join(list(tail)[-15:]), flush=True)
    return rc, list(tail)


# ============================================================================
# Inspect, Plan Gate, Pitch Card, Render & Downloads Delivery
# ============================================================================
def run_inspect(
    raw_inputs: List[str],
    script_path: Path,
    lang: str = "auto",
    preset: str = "auto",
    mark_pack: str = "flash",
    style: str = "",
    name: str = "",
    compact: bool = False,
) -> Path:
    clips = expand_clip_inputs(raw_inputs)
    if not clips:
        raise FileNotFoundError(f"No video clips found in: {raw_inputs}")

    project_name = common.resolve_user_project_name(script_path.stem)
    work_dir = WORK / project_name
    common.stamp_job_owner(work_dir)
    _archive_call("mark_started", work_dir)

    tracker = ProgressTracker(work_dir=work_dir, project_name=project_name, heartbeat_sec=48.0, video_name=name)
    tracker.start_heartbeat()
    found_edl: List[Path] = []
    done_clips = [0]

    def _on_prep_line(line: str) -> None:
        low = line.lower()
        if "transcrib" in low or "words.json" in low or "clip " in low:
            done_clips[0] = min(len(clips), done_clips[0] + 1)
            tracker.update(stage=2, detail=f"Transcribing & aligning clip {done_clips[0]}/{len(clips)}", completed=done_clips[0], total=len(clips), emit_now=False)
        elif "line_map" in low or "director_packet" in low or "editing_packet" in low or "[packet]" in low:
            tracker.update(stage=3, detail="Building editing_packet.md", completed=len(clips), total=len(clips), pct=58, emit_now=False)
        m_edl = re.search(r"(work/[^\s]+/plan\.edl)", line)
        if m_edl:
            found_edl.append((ROOT / m_edl.group(1)).resolve())

    try:
        tracker.update(stage=1, detail=f"Inspecting {len(clips)} clip(s) against '{script_path.name}'...", completed=0, total=len(clips), pct=10)
        cmd = [
            str(common.find_venv_python()), "-u", str(ROOT / "scripts" / "prepare.py"),
            *[str(c) for c in clips],
            "--script", str(script_path),
            "--lang", lang,
            "--preset", preset,
            "--mark-pack", mark_pack,
            "--project", project_name,
            *(["--style", style] if style else []),
            *(["--name", name] if name else []),
        ]
        rc, _tail = _stream_subprocess(cmd, stage_label=f"inspect-{project_name}", compact=compact, on_line=_on_prep_line)
        if rc != 0:
            tracker.stop_heartbeat(final_state="error", final_detail=f"prepare.py exited with {rc}")
            _archive_call("event", work_dir, "failed", stage="inspect", rc=rc)
            _archive_call("archive_blocked", "inspect", f"prepare.py exited with code {rc}", script_arg=str(script_path), inputs=[str(c) for c in clips])
            raise RuntimeError(f"prepare.py failed with exit code {rc}")

        plan_path = found_edl[-1] if (found_edl and found_edl[-1].is_file()) else (work_dir / "plan.edl")
        if not plan_path.is_file():
            edls = sorted(WORK.glob("*/plan.edl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not edls:
                raise FileNotFoundError("Could not locate generated work/<project>/plan.edl")
            plan_path = edls[0].resolve()
        pkt_name = _packet_path(plan_path.parent).name
        tracker.stop_heartbeat(final_state="measured", final_detail=f"Footage measured; {pkt_name} ready")
        _archive_call("event", work_dir, "inspect", clips=len(clips))
        _archive_call("send_in_background", plan_path, "inspect")
        return plan_path.resolve()
    except Exception as exc:
        tracker.stop_heartbeat(final_state="error", final_detail=str(exc))
        raise


def print_inspect_guide(plan_path: Path, name: str = "") -> None:
    proj_dir = plan_path.parent
    rel_dir = _rel_workspace_path(proj_dir)
    rel_plan = _rel_workspace_path(plan_path)
    packet_name = _packet_path(proj_dir).name
    sheets = [f"{rel_dir}/{p.name}" for p in sorted(proj_dir.glob("clip_sheet_*.jpg"))]
    sheets_str = ", ".join(sheets) if sheets else "(no clip_sheet_*.jpg generated)"
    vname = _infer_video_name(load_edl(plan_path), plan_path, name)
    target_dl = _target_delivery_display(vname)
    py_cmd = _venv_py_cmd()
    guide = [
        f"\n[NEXT STEP] Footage measured in {rel_dir} — Host-AI Direct Guide:",
        f"[TURN 1] READ ONCE (1 batch): {rel_dir}/{packet_name} + {sheets_str}",
        "[DO NOT] Never open formats/*, scripts/*, facts.md, line_map.md, or clip_caches.json.",
        f"[TURN 2] WRITE ANALYSIS: {rel_dir}/script_analysis.md (first line: `Format: <id> (<title>)`).",
        f"[TURN 2] WRITE PLAN: {rel_plan} (every spoken line once in order, `sp=\"Name\"`, all splits `z=1`).",
        f"[TURN 2] OPTIONAL: {rel_dir}/decisions.txt (`accept <id> <reason>` only for genuine unscripted gaps).",
        f"[TURN 3] EXECUTE & DELIVER: {py_cmd} scripts/zeno.py --execute {rel_plan} --square --compact",
        f"[TURN 3] FIX ONE LINE:     {py_cmd} scripts/edit_packet.py {rel_dir} --line <ID> (avoid re-reading full packet on repairs)",
        "[WAIT]   End turn if backgrounded and wait for async wakeup (never poll sleep/stdin/tasks).",
        f"[TURN 4] Inspect {target_dl}review_sheet.jpg once, then start next video.",
    ]
    print("\n".join(guide), flush=True)


def _segment_duration(seg: Dict[str, Any]) -> float:
    fn = getattr(common, "segment_duration", None)
    if callable(fn):
        return float(fn(seg))
    ht_fn = getattr(common, "segment_hold_tail", None)
    hold_tail = float(ht_fn(seg)) if callable(ht_fn) else max(0.0, min(1.5, float(seg.get("hold_tail") or 0.0)))
    try:
        spd = float(seg.get("speed") or 1.0)
        if spd <= 0.0:
            spd = 1.0
    except (TypeError, ValueError):
        spd = 1.0
    spd = max(0.5, min(2.0, spd))
    return max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))) / spd + hold_tail


def _resolve_plan_script_path(plan: Dict[str, Any], plan_path: Path) -> Optional[Path]:
    script = plan.get("script")
    side = plan_path.parent / "plan.json"
    if not script and side.is_file():
        script = common.read_json(side).get("script")
    if not script:
        return None
    return common.resolve(script)


def order_gate(plan: Dict[str, Any], plan_path: Path) -> str:
    import script_order

    script_path = _resolve_plan_script_path(plan, plan_path)
    if script_path is None:
        return "[BLOCKED] Missing #script header in plan.edl."
    g = script_order.full_gate(plan, script_path, plan_path.parent, plan_path)
    try:
        import job_report
        job_report.record_plan_check(plan_path.parent, g)
    except Exception:
        pass
    if g.get("voice_note"):
        print(f"[VOICE] {g['voice_note']}", flush=True)
    for t in g.get("tighten") or []:
        print(f"[TIGHT] {t}", flush=True)
    for t in g.get("style") or []:
        print(f"[STYLE] {t}", flush=True)
    return script_order.format_gate(g) if g.get("blocked") else ""


def build_editorial_pitch_card(work_dir: Path, plan_path: Path) -> str:
    plan = load_edl(plan_path)
    segs = plan.get("segments", [])
    marks = list(plan.get("marks", []) or [])
    cards_cnt = 0
    for s in segs:
        marks.extend(s.get("marks", []) or [])
        cards_cnt += len(s.get("flashcards") or [])
    total_dur = sum(max(0.0, _segment_duration(s)) for s in segs)
    mm, ss = int(total_dur) // 60, total_dur - (int(total_dur) // 60) * 60
    layouts = sorted({str(s.get("layout") or "single") for s in segs}) or ["single"]
    speakers = list(dict.fromkeys(str(s.get("speaker") or "").strip() for s in segs if str(s.get("speaker") or "").strip()))
    vname = _infer_video_name(plan, plan_path)
    dl_dir = _target_delivery_display(vname)
    cards_str = f" | {cards_cnt} flashcard(s)" if cards_cnt else ""
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        f"║  🎬 ZENO EDITORIAL PITCH — {vname[:42]:<42} ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        f"  • Speakers   : {', '.join(speakers) if speakers else 'Primary Speaker'}",
        f"  • Layouts    : {', '.join(layouts)}",
        f"  • Timeline   : {len(segs)} pause-snapped cuts -> {mm:02d}:{ss:05.2f} ({total_dur:.2f}s) | {len(marks)} mark(s){cards_str}",
        f"  • Target Dir : {dl_dir} (reel + SQ + Premiere .prproj/.xml + review_sheet.jpg)",
        "────────────────────────────────────────────────────────────────────────",
    ]
    text = "\n".join(lines)
    try:
        (work_dir / "pitch_card.md").write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    return text


def apply_stage3_tweaks(
    plan_path: Path,
    swap_layout: Optional[str] = None,
    swap_takes: Optional[List[str]] = None,
    error_sfx: Optional[str] = None,
) -> None:
    if not swap_layout and not swap_takes and not error_sfx:
        return
    plan = edl_parser.load_edl(plan_path)
    changed = False
    if swap_layout:
        plan["preset"] = swap_layout
        for s in plan.get("segments", []):
            if swap_layout == "single":
                s.pop("layout", None)
            elif s.get("layout") in ("split_h", "split_v", "chat_bar", "video_call", "top_pip") or swap_layout in ("split_h", "split_v"):
                s["layout"] = swap_layout
        changed = True
    if swap_takes:
        for item in swap_takes:
            if "=" in item:
                seg_id, new_src = [p.strip() for p in item.split("=", 1)]
                for s in plan.get("segments", []):
                    if str(s.get("id")) == seg_id:
                        s["src"] = new_src
                        changed = True
    if error_sfx:
        plan["error_sfx"] = error_sfx
        for m in plan.get("marks", []):
            if str(m.get("type", "")).lower() in ("wrong", "x", "cross", "incorrect", "error", "bad"):
                m["sfx"] = error_sfx
        changed = True
    if changed:
        plan_path.write_text(edl_parser.format_edl(plan), encoding="utf-8")
        common.write_json(plan_path.with_suffix(".json"), plan)


def _deliver_package(plan_path: Path, name: str) -> Tuple[Path, List[str]]:
    plan = load_edl(plan_path)
    effective_name = _infer_video_name(plan, plan_path, name)
    import deliver

    res = deliver.deliver_to_downloads(plan_path, effective_name)
    if isinstance(res, dict):
        folder = Path(str(res.get("folder") or res.get("dest") or _target_delivery_dir(effective_name)))
        files = [Path(str(f)).name for f in (res.get("files") or [])]
    else:
        folder = Path(str(res))
        files = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
    if not files and folder.is_dir():
        files = sorted(p.name for p in folder.iterdir() if p.is_file())
    return folder, files


def run_execute_and_deliver(
    plan_path: Path,
    name: str = "",
    square: bool = True,
    loudness: Optional[float] = None,
    gain_db: Optional[float] = None,
    draft: bool = False,
    swap_layout: Optional[str] = None,
    swap_takes: Optional[List[str]] = None,
    error_sfx: Optional[str] = None,
    compact: bool = False,
) -> Tuple[int, Optional[Path], str]:
    p_path = common.resolve(str(plan_path))
    if not p_path.is_file():
        _archive_call("archive_blocked", "execute", f"Edit plan not found: {plan_path}")
        return 2, None, f"Edit plan not found: {plan_path}"

    apply_stage3_tweaks(p_path, swap_layout=swap_layout, swap_takes=swap_takes, error_sfx=error_sfx)
    plan = load_edl(p_path)
    blocked = order_gate(plan, p_path)
    _archive_call("snapshot_plan", p_path, "render", note=blocked or "pass")
    if blocked:
        print(blocked, flush=True)
        print("\n[BLOCKED] Render stopped by plan gate. Fix plan.edl and run --execute again.", flush=True)
        reason = blocked.splitlines()[0]
        _capture_token_session(p_path.parent)
        _archive_call("event", p_path.parent, "blocked", reason=reason)
        _archive_call(
            "archive_job",
            p_path,
            plan=plan,
            video_seconds=0.0,
            output_mp4=None,
            kind="blocked",
            extra={"error": blocked},
        )
        return 2, None, reason

    import script_order

    moved = script_order.snap_cuts(plan)
    if moved:
        print(f"[SNAP] {moved} cut boundary(s) snapped to quietest breath gap.", flush=True)
    script_path = _resolve_plan_script_path(plan, p_path)
    fc_fn = getattr(script_order, "calibrate_flashcards", None)
    cards_calibrated = (
        int(fc_fn(plan, script_path, p_path.parent) or 0)
        if callable(fc_fn)
        else 0
    )
    if cards_calibrated:
        print(f"[CARDS] {cards_calibrated} flashcard(s) calibrated.", flush=True)
    cal_fn = getattr(script_order, "calibrate_post_word_beats", None)
    calibrated = (
        int(cal_fn(plan, script_path, p_path.parent) or 0)
        if callable(cal_fn) and script_path is not None
        else 0
    )
    if calibrated:
        print(f"[BEATS] {calibrated} post-word beat(s) calibrated.", flush=True)
    if moved or cards_calibrated or calibrated:
        p_path.write_text(edl_parser.format_edl(plan), encoding="utf-8")

    print("[PLAN PASS] Every line present, once, in script order; no cut inside a word; voices match.", flush=True)
    print(build_editorial_pitch_card(p_path.parent, p_path), flush=True)

    total_segs = max(1, len(plan.get("segments", [])))
    tracker = ProgressTracker(work_dir=p_path.parent, project_name=p_path.parent.name, heartbeat_sec=48.0, resume=True)
    tracker.start_heartbeat()
    done_segs = [0]

    def _on_render_line(line: str) -> None:
        low = line.lower()
        if "segment" in low or re.search(r"\bl\d+[a-z]?\b", low):
            done_segs[0] = min(total_segs, done_segs[0] + 1)
            pct = 65 + int(round(22.0 * (done_segs[0] / total_segs)))
            tracker.update(stage=4, detail=f"Rendering cut {done_segs[0]}/{total_segs}", completed=done_segs[0], total=total_segs, pct=pct, emit_now=False)
        elif "1:1 square" in low or "premiere" in low or "[verify" in low:
            tracker.update(stage=5, detail="Mastering 1:1 Square, Premiere .prproj & Deep QC", completed=total_segs, total=total_segs, pct=92, emit_now=False)

    cmd = [str(common.find_venv_python()), "-u", str(ROOT / "scripts" / "render.py"), str(p_path), "--verify"]
    if square:
        cmd.append("--square")
    if draft:
        cmd.append("--draft")
    if loudness is not None:
        cmd.extend(["--loudness", str(loudness)])
    if gain_db is not None:
        cmd.extend(["--gain-db", str(gain_db)])

    completed = False
    try:
        rc, _tail = _stream_subprocess(cmd, stage_label=f"render-{p_path.parent.name}", compact=compact, on_line=_on_render_line)
        if rc != 0:
            tracker.stop_heartbeat(final_state="error", final_detail=f"render.py exited with {rc}")
            completed = True
            reason = f"render.py exited with code {rc}"
            _capture_token_session(p_path.parent)
            _archive_call("event", p_path.parent, "failed", stage="render", rc=rc, reason=reason)
            _archive_call(
                "archive_job",
                p_path,
                plan=plan,
                video_seconds=0.0,
                output_mp4=None,
                kind="failed",
                extra={"error": reason, "rc": rc},
            )
            return rc, None, reason

        folder, files = _deliver_package(p_path, name)
        tracker.stop_heartbeat(final_state="completed", final_detail=f"Delivered to {_pretty_path(folder)}")
        completed = True
    except Exception as exc:
        if not completed:
            tracker.stop_heartbeat(final_state="error", final_detail=str(exc))
            completed = True
        reason = str(exc) or type(exc).__name__
        _capture_token_session(p_path.parent)
        _archive_call("event", p_path.parent, "failed", stage="execute", reason=reason)
        _archive_call(
            "archive_job",
            p_path,
            plan=plan,
            video_seconds=0.0,
            output_mp4=None,
            kind="failed",
            extra={"error": reason},
        )
        raise
    finally:
        if not completed:
            tracker.stop_heartbeat(final_state="error", final_detail="Render interrupted")

    common.job_done()
    print(f"\n[DELIVERY] {_pretty_path(folder)} ({folder})", flush=True)
    if files:
        print(f"[OUTPUT]   {', '.join(files)}", flush=True)

    vid_sec = sum(max(0.0, _segment_duration(s)) for s in plan.get("segments", []))
    telemetry_out, archive_out = _resolve_output_mp4(p_path, plan, folder=folder)
    _finalize_execute_telemetry(p_path, plan, vid_sec, telemetry_out)
    _archive_call("event", p_path.parent, "render", video_seconds=round(vid_sec, 2), output=str(archive_out))
    _archive_call(
        "archive_job",
        p_path,
        plan=plan,
        video_seconds=vid_sec,
        output_mp4=archive_out,
        kind="render",
    )
    return 0, folder, ""


# ============================================================================
# Multi-Video Sequential Queue Runner (`--queue <queue.json>`)
# ============================================================================
def _load_queue_jobs(queue_path: Path) -> List[Dict[str, Any]]:
    p = common.resolve(str(queue_path))
    if not p.is_file():
        raise FileNotFoundError(f"Queue JSON file not found: {queue_path}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        jobs = raw
    elif isinstance(raw, dict):
        jobs = raw.get("jobs") or raw.get("queue") or raw.get("videos") or []
    else:
        jobs = []
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(f"No jobs found in queue file: {p}")
    return [j for j in jobs if isinstance(j, dict)]


def run_queue(
    queue_file: str,
    inspect_only: bool = False,
    default_style: str = "",
    default_preset: str = "auto",
    default_lang: str = "auto",
    default_mark_pack: str = "flash",
    default_square: bool = True,
    skip_preflight: bool = False,
    compact: bool = False,
) -> int:
    jobs = _load_queue_jobs(Path(queue_file))
    total = len(jobs)
    ok_sys, sys_reason = run_system_preflight(skip=skip_preflight)
    if not ok_sys:
        print(f"[BLOCKED] System preflight failed before starting queue: {sys_reason}", flush=True)
        _archive_call("archive_blocked", "queue_preflight", sys_reason)
        return 2

    py_cmd = _venv_py_cmd()
    results: List[Dict[str, Any]] = []
    print(f"\n[QUEUE START] Preparing {total} video job(s) sequentially (Host-AI Direct)...", flush=True)

    for idx, job in enumerate(jobs, 1):
        raw_footage = job.get("footage") or job.get("inputs") or job.get("clips") or job.get("src_dir") or job.get("raw_dir")
        job_inputs = [str(x) for x in raw_footage] if isinstance(raw_footage, list) else ([str(raw_footage)] if raw_footage else [])
        job_script = str(job.get("script") or job.get("script_url") or job.get("gdoc") or "").strip()
        job_name = str(job.get("name") or job.get("video_name") or job.get("title") or f"Video_{idx}").strip()
        job_style = str(job.get("format") or job.get("style") or default_style or "").strip()
        job_preset = str(job.get("preset") or default_preset or "auto").strip()
        job_lang = str(job.get("lang") or job.get("language") or default_lang or "auto").strip()
        job_mark_pack = str(job.get("mark_pack") or default_mark_pack or "flash").strip()
        job_square = bool(job.get("square", default_square))

        print(f"\n[QUEUE {idx}/{total} START] {job_name}", flush=True)
        t0 = time.time()
        proj_rel = ""
        try:
            if not job_inputs or not job_script:
                raise ValueError("Job requires 'footage' and 'script'")
            common.job_begin(identity=json.dumps([job_script, job_name, job_inputs], ensure_ascii=False))
            script_path, inp_err = validate_job_inputs(job_inputs, job_script, skip=skip_preflight)
            if inp_err:
                raise RuntimeError(inp_err)

            plan_path = run_inspect(
                raw_inputs=job_inputs,
                script_path=script_path,
                lang=job_lang,
                preset=job_preset,
                mark_pack=job_mark_pack,
                style=job_style,
                name=job_name,
                compact=compact,
            )
            proj_dir = plan_path.parent
            proj_rel = _rel_workspace_path(proj_dir)
            plan_rel = _rel_workspace_path(plan_path)
            rel_pkt = _rel_workspace_path(_packet_path(proj_dir))
            target_dl = _target_delivery_display(job_name)
            sq_flag = "--square" if job_square else "--no-square"
            exec_hint = f"{py_cmd} scripts/zeno.py --execute {plan_rel} {sq_flag} --compact"
            print(
                f"[QUEUE {idx}/{total} PREPARED] {job_name}\n"
                f"  • Project : {proj_rel}\n"
                f"  • Packet  : {rel_pkt}\n"
                f"  • Plan    : {plan_rel}\n"
                f"  • Target  : {target_dl}\n"
                f"  • Execute : {exec_hint}",
                flush=True,
            )
            results.append(
                {
                    "index": idx,
                    "name": job_name,
                    "project": proj_rel,
                    "packet": rel_pkt,
                    "plan": plan_rel,
                    "target": target_dl,
                    "execute": exec_hint,
                    "status": "prepared",
                    "seconds": round(time.time() - t0, 1),
                }
            )
        except Exception as exc:
            reason = " ".join(str(exc).split())[:280] or type(exc).__name__
            print(f"[QUEUE {idx}/{total} FAILED] {job_name} — {reason} (continuing queue)", flush=True)
            _archive_call("archive_blocked", "queue_inspect", reason, script_arg=job_script, inputs=job_inputs)
            results.append(
                {
                    "index": idx,
                    "name": job_name,
                    "project": proj_rel,
                    "status": "failed",
                    "error": reason,
                    "seconds": round(time.time() - t0, 1),
                }
            )

    WORK.mkdir(parents=True, exist_ok=True)
    common.write_json(
        WORK / "queue_report.json",
        {"total": total, "inspect_only": inspect_only, "jobs": results, "finished_at": time.time()},
    )
    ok_cnt = sum(1 for r in results if r["status"] in ("prepared", "inspected", "done"))
    fail_cnt = total - ok_cnt
    print(f"\n[QUEUE SUMMARY] {ok_cnt}/{total} prepared, {fail_cnt} failed:", flush=True)
    for r in results:
        if r["status"] in ("prepared", "inspected"):
            print(
                f"  ✓ [{r['index']}/{total}] {r['name']} | {r['project']} | {r['packet']} | {r['plan']} -> {r['target']}",
                flush=True,
            )
            print(f"      Execute: {r['execute']}", flush=True)
        else:
            print(f"  ✗ [{r['index']}/{total}] {r['name']} — {r.get('error')}", flush=True)

    if total >= 2:
        print(
            "\n[MULTI-VIDEO GUIDE] >=2 videos queued — isolate each video inside 1 subagent to keep the main chat clean:\n"
            "  • Per video subagent: read ONLY <project>/editing_packet.md + <project>/clip_sheet_*.jpg once,\n"
            "    write <project>/script_analysis.md + <project>/plan.edl, then run:\n"
            f"    {py_cmd} scripts/zeno.py --execute <project>/plan.edl --square --compact\n"
            "  • Return only the delivered path (~/Downloads/Zeno Outs/<Name>/) and status to the main chat.",
            flush=True,
        )
    return 0 if fail_cnt == 0 else 2


# ============================================================================
# CLI Entrypoint
# ============================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Zeno — Unified Host-AI Direct & Multi-Video Queue Editor")
    ap.add_argument("inputs", nargs="*", help="Raw footage folder or clip paths")
    ap.add_argument("--script", default=None, help="Local script path or Google Doc ?tab=t.xxxx URL")
    ap.add_argument("--name", default="", help="Video delivery name -> ~/Downloads/Zeno Outs/<Name>/")
    ap.add_argument("--format", "--style", dest="style", default="", help="Format ID (e.g. skit, stop_saying, scrolling_text)")
    ap.add_argument("--lang", default="auto", help="Language code (auto, kn, ta, te, ml, hi, en)")
    ap.add_argument("--preset", default="auto", help="Layout preset (auto, single, split_h, split_v, top_pip, chat_bar, video_call, hybrid)")
    ap.add_argument("--mark-pack", default="flash", help="Mark overlay pack (flash, vignette, glossy, default)")
    ap.add_argument("--inspect", action="store_true", help="Stages 1-3: Measure footage, build editing_packet.md & print Host-AI Direct guide")
    ap.add_argument("--queue", default=None, metavar="QUEUE_JSON", help="Sequential multi-video queue JSON file (Host-AI Direct prepare mode)")
    ap.add_argument("--inspect-only", action="store_true", help="With --queue: prepare all queued packets upfront (default queue mode)")
    ap.add_argument("--check-plan", default=None, metavar="PLAN_EDL", help="Validate plan.edl order, cuts & voices (exit 0 pass, 2 blocked)")
    ap.add_argument("--execute", "--check-and-execute", default=None, metavar="PLAN_EDL", help="Validate, render & deliver plan.edl to ~/Downloads/Zeno Outs/<Name>/")
    ap.add_argument("--pitch", default=None, metavar="PLAN_EDL", help="Print Editorial Pitch Card for plan.edl")
    ap.add_argument("--preflight", action="store_true", help="Run live system & auth preflight checks")
    ap.add_argument("--check", action="store_true", help="Run Edit Bay readiness check")
    ap.add_argument("--square", action="store_true", default=True, help="Export 1:1 Square alongside 9:16 Master (default: True)")
    ap.add_argument("--no-square", action="store_false", dest="square", help="Skip 1:1 Square export")
    ap.add_argument(
        "--loudness",
        type=float,
        default=None,
        help=f"Target loudness in LUFS (default: {common.DEFAULT_LOUDNESS_LUFS:.1f} LUFS)",
    )
    ap.add_argument("--gain-db", type=float, default=None, help="Additional master gain in dB")
    ap.add_argument("--draft", action="store_true", help="Fast draft encode")
    ap.add_argument("--swap-layout", choices=("single", "split_h", "split_v", "top_pip", "chat_bar", "video_call"), default=None)
    ap.add_argument("--swap-take", action="append", default=None, metavar="SEG_ID=CLIP")
    ap.add_argument("--error-sfx", default=None, metavar="SFX_FILE")
    ap.add_argument("--feedback", default=None, metavar="TEXT", help="Log editor revision feedback on a project")
    ap.add_argument("--plan", default=None, metavar="PLAN_EDL", help="Target plan.edl for --feedback")
    ap.add_argument("--login", action="store_true", help="Sign in to Zeno Cloud via Google OAuth")
    ap.add_argument("--whoami", action="store_true", help="Show active Zeno Cloud session")
    ap.add_argument("--fresh", action="store_true", help="Rebuild cached transcripts and alignments")
    ap.add_argument("--skip-preflight", action="store_true", help="Development only: skip preflight checks")
    ap.add_argument("--compact", action="store_true", help="Concise console output; full subprocess logs saved to work/_logs/")
    args = ap.parse_args()

    if args.fresh:
        os.environ["ZENO_FRESH"] = "1"

    if args.login:
        import auth_session
        sys.exit(0 if auth_session.interactive_login() else 1)

    if args.whoami:
        import auth_session
        info = auth_session.get_session_info()
        who = info.get("email") or info.get("name") or info.get("mode") or "unknown"
        print(f"[AUTH] Signed in as: {who}" if info.get("authenticated") else "[AUTH] Not signed in. Run: .venv/bin/python scripts/setup.py --login")
        sys.exit(0)

    if args.check and not (args.inspect or args.execute or args.queue or args.inputs):
        ok, _ = check_system_readiness(quiet=False)
        sys.exit(0 if ok else 1)

    has_action = bool(
        args.preflight
        or args.feedback
        or args.queue
        or args.check_plan
        or args.pitch
        or args.execute
        or args.inspect
        or (args.inputs and args.script)
    )
    if has_action:
        _maybe_auto_update()

    if args.preflight:
        ok, _reason = run_system_preflight(skip=False)
        if ok and args.script and args.inputs:
            _sp, inp_err = validate_job_inputs(args.inputs, args.script, skip=False)
            ok = inp_err is None
        print("\nSYSTEM: " + ("ALL CHECKS PASSED." if ok else "NOT READY — fix FAIL lines above."))
        sys.exit(0 if ok else 1)

    if args.feedback:
        plans = [common.resolve(args.plan)] if args.plan else sorted(WORK.glob("*/plan.edl"), key=lambda p: p.stat().st_mtime)
        if not plans or not plans[-1].is_file():
            print("[BLOCKED] No job found; pass --plan work/<project>/plan.edl")
            sys.exit(2)
        plan_p = plans[-1]
        fb_dir = plan_p.parent
        saved = _archive_call("add_feedback", fb_dir, args.feedback, source="editor")
        if saved is not True and args.feedback.strip():
            with (fb_dir / "feedback.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": round(time.time(), 1), "source": "editor", "text": args.feedback}, ensure_ascii=False) + "\n")
        _capture_token_session(fb_dir)
        try:
            fb_plan = load_edl(plan_p)
        except Exception:
            fb_plan = None
        _archive_call("event", fb_dir, "feedback", text=args.feedback)
        _archive_call("archive_job", plan_p, plan=fb_plan, kind="feedback")
        print(f"[FEEDBACK] Saved revision note for {fb_dir.name}")
        sys.exit(0)

    if args.queue:
        sys.exit(
            run_queue(
                args.queue,
                inspect_only=args.inspect_only,
                default_style=args.style,
                default_preset=args.preset,
                default_lang=args.lang,
                default_mark_pack=args.mark_pack,
                default_square=args.square,
                skip_preflight=args.skip_preflight,
                compact=args.compact,
            )
        )

    if args.check_plan:
        p_path = common.resolve(args.check_plan)
        msg = order_gate(load_edl(p_path), p_path)
        _archive_call("snapshot_plan", p_path, "check", note=msg or "pass")
        _archive_call("event", p_path.parent, "check", passed=not bool(msg))
        _archive_call("send_in_background", p_path, "checking")
        print(msg or "[PLAN PASS] Every line present, once, in script order; no cut inside a word; voices match.")
        sys.exit(2 if msg else 0)

    if args.pitch:
        p_path = common.resolve(args.pitch)
        apply_stage3_tweaks(p_path, swap_layout=args.swap_layout, swap_takes=args.swap_take, error_sfx=args.error_sfx)
        _archive_call("snapshot_plan", p_path, "pitch")
        print(build_editorial_pitch_card(p_path.parent, p_path))
        sys.exit(0)

    if args.execute:
        rc, _folder, _err = run_execute_and_deliver(
            common.resolve(args.execute),
            name=args.name,
            square=args.square,
            loudness=args.loudness,
            gain_db=args.gain_db,
            draft=args.draft,
            swap_layout=args.swap_layout,
            swap_takes=args.swap_take,
            error_sfx=args.error_sfx,
            compact=args.compact,
        )
        sys.exit(rc)

    if args.inspect or (args.inputs and args.script):
        if not args.inputs or not args.script:
            ap.error("Provide <footage_dir_or_clips> and --script <path_or_gdoc_tab_url>")
        common.job_begin(identity=json.dumps([args.script, args.name, args.inputs], ensure_ascii=False))
        ok_sys, sys_reason = run_system_preflight(skip=args.skip_preflight)
        if not ok_sys:
            _archive_call("archive_blocked", "preflight", sys_reason, script_arg=args.script, inputs=args.inputs)
            print(f"\n[BLOCKED] Live system check failed: {sys_reason}", flush=True)
            sys.exit(2)
        try:
            script_path, inp_err = validate_job_inputs(args.inputs, args.script, skip=args.skip_preflight)
        except Exception as exc:
            _archive_call("archive_blocked", "inputs", str(exc), script_arg=args.script, inputs=args.inputs)
            print(f"\n[BLOCKED] Script error: {exc}", flush=True)
            sys.exit(2)
        if inp_err:
            _archive_call("archive_blocked", "inputs", inp_err, script_arg=args.script, inputs=args.inputs)
            print(f"\n[BLOCKED] Input validation failed: {inp_err}", flush=True)
            sys.exit(2)
        print("\n[GATE PASSED] System and inputs verified. Starting Zeno.", flush=True)
        plan_path = run_inspect(
            raw_inputs=args.inputs,
            script_path=script_path,
            lang=args.lang,
            preset=args.preset,
            mark_pack=args.mark_pack,
            style=args.style,
            name=args.name,
            compact=args.compact,
        )
        print_inspect_guide(plan_path, name=args.name)
        sys.exit(0)

    ap.print_help()


if __name__ == "__main__":
    main()
