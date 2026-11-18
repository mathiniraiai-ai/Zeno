#!/usr/bin/env python3
"""
Zeno — Standalone & Conversational 5-Stage AI Video Editor Harness (`scripts/zeno.py`).

Modes:
  1. System Readiness Check (Stage 0):
       python3 scripts/zeno.py --check
  2. Pre-Flight Inspect & Editorial Pitch Card (Stages 1–3):
       python3 scripts/zeno.py --inspect <raw_dir_or_clips...> --script <path_or_gdoc_url> [--lang auto] [--preset auto]
  3. Instant Pitch Card from Existing Work Folder:
       python3 scripts/zeno.py --pitch work/<project>/plan.edl
  4. Execute Edit, Render, Premiere .prproj Export & Deep QC (Stages 4–5):
       python3 scripts/zeno.py --execute work/<project>/plan.edl [--square]
  5. Interactive End-to-End Editor (Stage 0 -> Stages 1–3 -> [Y/n] Gate -> Stages 4–5):
       python3 scripts/zeno.py <raw_dir_or_clips...> --script <path_or_gdoc_url> [--square]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request
from typing import List, Optional, Tuple

import common
import edl_parser
from edl_parser import load_edl
from pathlib import Path
from progress import ProgressTracker

# Auto-switch into the project's .venv Python if invoked via system python3
_VENV_PY = common.find_venv_python()
if (
    _VENV_PY
    and os.path.exists(_VENV_PY)
    and os.path.realpath(sys.executable) != os.path.realpath(str(_VENV_PY))
    and not os.environ.get("_ZENO_REEXEC")
):
    os.environ["_ZENO_REEXEC"] = "1"
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

# Keep the computer cool: low priority for the whole job (GPU does the heavy work), see common.run_cool
if __name__ == "__main__":
    common.run_cool()

ROOT = common.ROOT
WORK = common.WORK
OUTPUT = common.OUTPUT

GDOC_RE = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")


# ============================================================================
# STAGE 0: System & Edit Bay Readiness Check
# ============================================================================
def check_system_readiness(quiet: bool = False) -> Tuple[bool, List[Tuple[bool, str, str]]]:
    """
    Inspect all 8 Zeno Edit Bay dependencies without ever reading or printing secret keys.
    Returns `(all_ok, [(ok, label, detail), ...])`.
    """
    checks: List[Tuple[bool, str, str]] = []

    # 1. Python & Virtual Environment
    venv_py = common.find_venv_python()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    has_venv = (ROOT / ".venv").is_dir()
    checks.append((
        True,
        "Python & Venv",
        f"Python {py_ver} ({'.venv active' if has_venv else 'system python'})",
    ))

    # 2. FFmpeg & FFprobe
    ff_ok = bool(common.FFMPEG and common.FFPROBE and os.path.exists(common.FFMPEG) and os.path.exists(common.FFPROBE))
    checks.append((
        ff_ok,
        "FFmpeg & FFprobe",
        f"{common.FFMPEG}" if ff_ok else "Missing ffmpeg/ffprobe binary (run: python3 scripts/setup.py)",
    ))

    # 3. Core Python Media Packages
    missing_pkgs = []
    for mod_name, pkg_label in (
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("PIL", "Pillow"),
        ("scipy", "scipy"),
    ):
        try:
            __import__(mod_name)
        except ImportError:
            missing_pkgs.append(pkg_label)
    checks.append((
        len(missing_pkgs) == 0,
        "Media & CV Packages",
        "opencv, numpy, Pillow, scipy installed" if not missing_pkgs else f"Missing: {', '.join(missing_pkgs)}",
    ))

    # 4. YuNet ONNX Face Detector
    yunet_ok = common.YUNET_MODEL.is_file() and common.YUNET_MODEL.stat().st_size > 100_000
    checks.append((
        yunet_ok,
        "YuNet Face Tracker",
        f"{common.YUNET_MODEL.name} ready" if yunet_ok else "Missing models/face_detection_yunet_2023mar.onnx",
    ))

    # 5. Transcription API Key / Session Presence (Zero-Secret Check via os.path.exists & os.environ booleans)
    has_session_file = os.path.exists(ROOT / ".zeno_session.json")
    has_env_file = os.path.exists(ROOT / ".env") or os.path.exists(common.SCRIPTCUT_ROOT / ".env")
    has_key = bool(
        has_session_file
        or os.environ.get("ZENO_CLOUD_TOKEN")
        or os.environ.get("SCRIPTCUT_CLOUD_TOKEN")
        or os.environ.get("ZENO_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    checks.append((
        has_key or has_env_file,
        "Zeno Cloud / Gemini Auth",
        "Connected via .zeno_session.json / .env (verified safely)" if (has_key or has_env_file) else "Run: python3 scripts/setup.py --login",
    ))

    # 6. Indic & Latin Fonts
    fonts_dir = common.FONTS_DIR
    ttf_files = list(fonts_dir.glob("*.ttf")) if fonts_dir.is_dir() else []
    mac_indic = list(pathlib.Path("/System/Library/Fonts/Supplemental").glob("*Sangam*.ttc")) if common.IS_MAC else []
    fonts_ok = len(ttf_files) >= 1 and (len(mac_indic) >= 1 or len(ttf_files) >= 3 or common.IS_WIN)
    checks.append((
        fonts_ok,
        "Indic & UI Fonts",
        f"{len(ttf_files)} bundled + {len(mac_indic)} system Indic font families ready" if fonts_ok else "Missing UI/Indic fonts",
    ))

    # 7. SFX, Mark Packs & Premiere .prproj Base Shell
    pr_shell = ROOT / "assets" / "premiere" / "base_1080x1920.prproj.gz"
    sfx_ok = (common.SFX_DIR / "whoosh.wav").is_file() and (common.MARKS_DIR / "red_flash.png").is_file() and pr_shell.is_file()
    checks.append((
        sfx_ok,
        "SFX, Marks & .prproj Shell",
        "flash/vignette marks + SFX + Premiere 2026 shell ready" if sfx_ok else "Missing assets in assets/sfx, assets/marks, or assets/premiere",
    ))

    # 8. Output Folder
    try:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        out_ok = OUTPUT.is_dir()
    except Exception:
        out_ok = False
    checks.append((
        out_ok,
        "Output Directory",
        str(OUTPUT),
    ))

    # 9. Style Tuning Config & Regression Suite
    try:
        import style_config
        t_cfg = style_config.get_tuning("micronarrative")
        cfg_ok = len(t_cfg) >= 25 and (ROOT / "tests" / "test_engine.py").is_file()
    except Exception:
        t_cfg = {}
        cfg_ok = False
    checks.append((
        cfg_ok,
        "Style Config & Tests",
        f"{len(t_cfg)} tunable parameters in spec.json + tests/test_engine.py ready" if cfg_ok else "Missing style_config.py or tests/test_engine.py",
    ))

    all_ok = all(item[0] for item in checks)
    if not quiet:
        installed = [c for c in checks if c[0]]
        remaining = [c for c in checks if not c[0]]
        print("╔══════════════════════════════════════════════════════════════════════╗")
        print("║                    ZENO EDIT BAY — READINESS CHECK                   ║")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        for ok, label, detail in checks:
            mark = "✓" if ok else "✗"
            print(f"  [{mark}] {label:<26} : {detail}")
        print("────────────────────────────────────────────────────────────────────────")
        if all_ok:
            print(f"  STATUS: READY ({len(installed)}/{len(checks)} installed, 0 remaining)")
        else:
            print(f"  STATUS: ACTION NEEDED ({len(installed)}/{len(checks)} installed, {len(remaining)} remaining)")
            for _, label, detail in remaining:
                print(f"    -> Fix {label}: {detail}")
            print("    -> Run: python3 scripts/setup.py")
        print()
    return all_ok, checks


# ============================================================================
# Google Doc & Input Clip Resolution
# ============================================================================
def resolve_script_input(script_arg: str) -> pathlib.Path:
    """Resolve a local script path, or save ONE tab/heading of a Google Doc link to input/script/ (never the whole document)."""
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
        raise ValueError(
            "This Google Doc link has no tab or heading, so it would load every language in the document. "
            "Open the tab that holds this script and share that link (it ends with ?tab=t.xxxx)."
        )
    try:
        text, _note = script_intake.fetch_google_doc(s)
    except SystemExit as exc:   # script_intake exits with a message when the section is missing
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Could not download Google Doc ({doc_id}): {exc}. Ensure link sharing is set to 'Anyone with the link can view'."
        ) from exc
    if len(text.strip()) < 10:
        raise RuntimeError("The linked tab is empty.")
    out_dir = ROOT / "input" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", info.get("tab") or info.get("heading") or "")[:24]
    dest = out_dir / f"gdoc_{doc_id[:12]}_{key}.txt"
    dest.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
    return dest


def style_hint(style: str) -> str:
    """One line about the job's style: confirm it exists, or say how to set it."""
    known = sorted(p.parent.name for p in (ROOT / "styles").glob("*/spec.json"))
    if not style:
        return f"[style] none set, so this job is not counted toward a style. Add --style <name> (known: {', '.join(known) or 'none'})"
    if style.lower() not in known:
        return f"[style] '{style}' is not a learned style (known: {', '.join(known) or 'none'}); the job will still run"
    return f"[style] {style.lower()}"


def _archive_blocked(stage: str, reason: str, script_arg: str = "", inputs: Optional[List[str]] = None) -> None:
    """A job that stopped before it had a plan still goes to the server, with what stopped it (never blocks or slows)."""
    try:
        import job_archive
        job_archive.archive_blocked(stage, reason, script_arg=script_arg, inputs=inputs)
    except Exception:
        pass


SESSION_LIMIT = 1_000_000   # multi-video queued chats up to 1M tokens; config/cost_controls.json owns policy
SESSION_WARN = 750_000


def session_gate(stage: str, block: bool, project_hint: str = "", script_arg: str = "", inputs: Optional[List[str]] = None,
                 isolated_planner: bool = False) -> None:
    """Every step the agent takes re-reads the whole chat, so an edit started in a chat that already holds earlier work
    costs several times more (16 Nov: chats of 450-600k made each job re-read 5-10M tokens instead of about 1M).
    At the start of a job (block=True) a chat over SESSION_LIMIT stops the job and asks for a new chat; later
    (block=False) a chat over SESSION_WARN only prints a warning. Unknown size (another agent, no log) never stops anything."""
    from cost_control import settings
    policy = settings()
    limit = int(os.environ.get("ZENO_SESSION_LIMIT") or policy["new_chat_context_limit"])
    if limit <= 0:
        raise ValueError("ZENO_SESSION_LIMIT must be positive")
    try:
        import agent_log
        s = agent_log.session_size()
    except Exception:
        s = None
    if not s:
        print("[COST] Chat context could not be measured. Use a fresh edit chat; unknown usage is not zero.")
        return
    k = s["tokens"] // 1000
    if block and isolated_planner and s["tokens"] > limit:
        print(f"[COST] Shared chat holds {k}k tokens. Automatic planning uses isolated requests; "
              "do not open the planning packet or retry manually in this chat. Host usage still counts.")
        return
    if block and s["tokens"] > limit:
        print(f"\n[BLOCKED] NEW CHAT NEEDED. This chat already holds {k}k input tokens."
              "\n  Open a fresh editing chat with the same script, footage and Notion name."
              "\n  Zeno keeps the measured clips and saved plans; no cache clearing is needed.")
        _archive_blocked("fresh_session", f"chat already held {s['tokens']} tokens ({s['steps']} steps) at {stage}", script_arg, inputs)
        sys.exit(2)
    if not block and s["tokens"] > SESSION_WARN:
        where = f" in {project_hint}" if project_hint else ""
        print(f"\n[TOKENS] This chat now holds {k}k tokens, and every step re-reads all of it. Finish this step, then make any further"
              f"\n  changes in a new chat: say 'continue the edit{where}: <the change>'.")


def job_gate(raw_inputs: List[str], script_arg: str, skip: bool = False, style: str = "",
             isolated_planner: bool = False) -> None:
    """Nothing starts until the live checks pass and the script and footage the user gave have been checked."""
    import preflight

    if skip:
        print("\n[WARN] --skip-preflight: live checks and input validation skipped (development only).")
        return
    session_gate("inspect", block=True, script_arg=script_arg, inputs=raw_inputs, isolated_planner=isolated_planner)
    print("\n== Pre-job gate: live checks ==")
    checks = preflight.run_system_checks()
    preflight.print_checks(checks)
    if not preflight.save_state(checks):
        print("\n[BLOCKED] A live check failed, so the edit did not start. Fix the FAIL lines above and run again.")
        _archive_blocked("preflight", "; ".join(f"{c[1]}: {c[2]}" for c in checks if isinstance(c, (list, tuple)) and len(c) >= 3 and not c[0])
                         or str(checks)[:3000], script_arg, raw_inputs)
        sys.exit(2)
    print("\n== Pre-job gate: your script and footage ==")
    try:
        script_path = resolve_script_input(script_arg)
    except Exception as exc:
        print(f"  [FAIL] script: {exc}")
        print("\n[BLOCKED] The script could not be loaded, so the edit did not start.")
        _archive_blocked("script", f"{type(exc).__name__}: {exc}", script_arg, raw_inputs)
        sys.exit(2)
    res = preflight.validate_inputs(script_path, raw_inputs)
    preflight.print_inputs_report(res)
    if res["errors"]:
        print("\n[BLOCKED] The script or footage has a problem, so the edit did not start. Fix the FAIL lines and run again.")
        _archive_blocked("inputs", json.dumps(res["errors"], ensure_ascii=False, default=str)[:6000], script_arg, raw_inputs)
        sys.exit(2)
    try:   # every job starts with the latest knowledge from the server; no git pull or redeploy is needed for it
        import format_library
        import knowledge_sync

        print(f"  [knowledge] {knowledge_sync.pull()['message']}; team formats: {format_library.sync_team()['message']}")
    except Exception:
        pass
    print("  " + style_hint(style))
    print("\n[GATE PASSED] Live checks and inputs are good. Starting the edit.")


def expand_clip_inputs(raw_inputs: List[str]) -> List[pathlib.Path]:
    """Expand directories or file paths into a sorted list of raw video clips."""
    clips: List[pathlib.Path] = []
    seen = set()
    for raw in raw_inputs:
        p = pathlib.Path(raw).expanduser().resolve()
        if p.is_dir():
            for ext in ("*.mp4", "*.MP4", "*.mov", "*.MOV"):
                for child in sorted(p.glob(ext)):
                    if child.is_file() and str(child) not in seen:
                        seen.add(str(child))
                        clips.append(child)
        elif p.is_file():
            if str(p) not in seen:
                seen.add(str(p))
                clips.append(p)
    return clips


# ============================================================================
# STAGE 3: Editorial Pitch Card Generator
# ============================================================================
def build_editorial_pitch_card(work_dir: pathlib.Path, plan_path: pathlib.Path) -> str:
    """
    Build a concise, human-editor-style Pre-Flight Editorial Pitch Card from
    `work/<project>/plan.edl`.
    """
    work_dir = pathlib.Path(work_dir).resolve()
    plan_path = pathlib.Path(plan_path).resolve()
    plan = load_edl(plan_path)

    segs = plan.get("segments", [])
    marks = list(plan.get("marks", []) or [])
    for s in segs:
        marks.extend(s.get("marks", []) or [])

    total_dur = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segs)
    mm = int(total_dur) // 60
    ss = total_dur - mm * 60

    # Detect Concept & Style
    has_banners = any(s.get("banners") or s.get("stop_saying") for s in segs)
    layouts_used = []
    for s in segs:
        lay = str(s.get("layout") or plan.get("preset") or "single")
        if lay not in layouts_used:
            layouts_used.append(lay)

    if has_banners and any(l in ("split_h", "split_v") for l in layouts_used):
        concept = '"Stop Saying" Micronarrative (Single Hook + Split-Screen Conversation)'
    elif any(l in ("split_h", "split_v") for l in layouts_used):
        concept = "Two-Character Split-Screen Conversation"
    elif "video_call" in layouts_used:
        concept = "Remote Video-Call (PiP) Dialogue"
    elif "chat_bar" in layouts_used:
        concept = "Interactive Chat-Bar Language Drill"
    else:
        concept = "Single-Speaker Teaching Monologue"

    # Extract Speakers & Clips
    speakers = []
    raw_clips = []
    peer_clips = []
    for s in segs:
        sp = str(s.get("speaker") or "").strip()
        if sp and sp not in speakers:
            speakers.append(sp)
        src_name = pathlib.Path(str(s.get("src") or s.get("clip") or "")).name
        if src_name and src_name not in raw_clips:
            raw_clips.append(src_name)
        peer = s.get("peer")
        if isinstance(peer, dict) and peer.get("src"):
            pname = pathlib.Path(str(peer["src"])).name
            if pname and pname not in peer_clips:
                peer_clips.append(pname)

    warnings = []
    try:
        order_msg = order_gate(plan, plan_path)
    except Exception as exc:   # noqa: BLE001
        order_msg = f"[ORDER] could not be measured: {exc}"
    if order_msg:
        warnings.insert(0, "CRITICAL: " + order_msg)

    # Summarize Sections
    hook_segs = [s for s in segs if str(s.get("layout", "single")) == "single"]
    split_segs = [s for s in segs if str(s.get("layout", "")) in ("split_h", "split_v", "video_call", "chat_bar")]
    hook_dur = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in hook_segs)
    split_dur = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in split_segs)

    right_count = sum(1 for m in marks if str(m.get("type") or m.get("kind") or "").lower() in ("right", "check"))
    wrong_count = sum(1 for m in marks if str(m.get("type") or m.get("kind") or "").lower() in ("wrong", "x", "cross", "error"))

    out_mp4 = pathlib.Path(str(plan.get("output") or common.unique_job_output(work_dir.name)))
    out_sq = common.square_job_output(out_mp4)
    base_stem = common.strip_ratio_prefix(out_mp4.stem)
    out_prproj = out_mp4.with_name(f"{base_stem}.prproj")
    out_xml = out_mp4.with_name(f"{base_stem}.premiere.xml")

    card_lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        f"║  🎬 ZENO EDITORIAL PITCH — {out_mp4.stem:<41} ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        f"  • Concept & Style   : {concept}",
        f"  • Speakers Detected : {', '.join(speakers) if speakers else 'Primary Speaker'}",
        f"  • Raw Clips Mapped  : {len(raw_clips)} primary clip(s) ({', '.join(raw_clips)})",
        f"  • Listening Video   : {'Live video loop (' + ', '.join(peer_clips) + ')' if peer_clips else 'N/A (Single screen)'}",
        f"  • Timeline Plan     : {len(segs)} pause-snapped cuts → {mm:02d}:{ss:05.2f} ({total_dur:.2f}s) @ 30fps",
    ]
    if hook_segs:
        banner_cnt = sum(len(s.get("banners") or []) for s in hook_segs)
        b_note = f" + {banner_cnt} stacked 'Stop Saying' strikethrough banners" if banner_cnt else ""
        card_lines.append(
            f"    - Hook / Single   : {len(hook_segs)} cut(s) ({hook_dur:.1f}s) | Dynamic Zooms + Transitions{b_note}"
        )
    if split_segs:
        split_modes = ", ".join(sorted({str(s.get("layout")) for s in split_segs}))
        card_lines.append(
            f"    - Split / Body    : {len(split_segs)} cut(s) ({split_dur:.1f}s) | Layout: {split_modes} (zoom=1.0 + live listener on V2)"
        )
    card_lines.append(
        f"  • Marks & Audio     : {wrong_count} ✗ Red Flash + {right_count} ✓ Green Flash (2-pulse) | -11.5 LUFS Master"
    )
    try:
        import brain_memory
        bm_rules = brain_memory.load_brain_memory("micronarrative")
    except Exception:
        bm_rules = []
    if bm_rules:
        card_lines.append(
            f"  • Brain Memory      : {len(bm_rules)} permanent editor rule(s) active (styles/micronarrative/brain_memory.json)"
        )
        for r in bm_rules[:6]:
            rid = str(r.get("id") or "RULE")
            rtxt = str(r.get("rule") or "")
            if len(rtxt) > 68:
                rtxt = rtxt[:65] + "..."
            card_lines.append(f"    🧠 [{rid}] {rtxt}")
    card_lines.extend([
        "  • Deliverables      :",
        f"    1. 9:16 Master MP4       -> {out_mp4}",
        f"    2. 1:1 Square Meta Ad    -> {out_sq}",
        f"    3. Premiere Pro Project  -> {out_prproj}",
        f"    4. Premiere XML Timeline -> {out_xml}",
    ])
    if warnings:
        card_lines.append("  • Pre-Flight Alerts :")
        for w in warnings:
            card_lines.append(f"    ⚠️  {w}")
    card_lines.append("────────────────────────────────────────────────────────────────────────")
    card_text = "\n".join(card_lines)

    try:
        (work_dir / "pitch_card.md").write_text(card_text + "\n", encoding="utf-8")
    except Exception:
        pass
    return card_text


# ============================================================================
# STAGES 1–3: Inspect, Map, Transcribe & Build Pitch Card
# ============================================================================
def run_inspect(
    raw_inputs: List[str],
    script_arg: str,
    lang: str = "auto",
    preset: str = "auto",
    mark_pack: str = "flash",
    style: str = "",
    name: str = "",
) -> pathlib.Path:
    try:
        import auto_updater
        auto_updater.ensure_latest_update(force=True, quiet=False)
    except Exception:
        pass
    clips = expand_clip_inputs(raw_inputs)
    if not clips:
        raise FileNotFoundError(f"No .mp4/.mov clips found in: {raw_inputs}")

    script_path = resolve_script_input(script_arg)
    project_name = common.resolve_user_project_name(script_path.stem)
    work_dir = WORK / project_name
    common.stamp_job_owner(work_dir)
    import time as _time
    t_inspect = _time.time()
    try:   # the job archive: when this edit began, and a stage log (see job_archive.py)
        import job_archive
        job_archive.mark_started(work_dir)
        job_archive.event(work_dir, "inspect_start", clips=len(clips), lang=lang, style=style or "")
    except Exception:
        pass

    tracker = ProgressTracker(work_dir=work_dir, project_name=project_name, heartbeat_sec=48.0, video_name=name)
    tracker.start_heartbeat()

    try:
        tracker.update(
            stage=1,
            detail=f"Mapped {len(clips)} raw clip(s) & script '{script_path.name}'; extracting 16kHz audio...",
            completed=0,
            total=len(clips),
            pct=10,
        )

        py_bin = common.find_venv_python()
        cmd = [
            str(py_bin),
            "-u",
            str(ROOT / "scripts" / "prepare.py"),
            *[str(c) for c in clips],
            "--script",
            str(script_path),
            "--lang",
            lang,
            "--preset",
            preset,
            "--mark-pack",
            mark_pack,
            "--project",
            project_name,
            *(["--style", style] if style else []),
            *(["--name", name] if name else []),
        ]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("ZENO_MAX_THREADS", str(common.ZENO_MAX_THREADS))

        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        done_clips = 0
        plan_path: Optional[pathlib.Path] = None
        assert proc.stdout is not None
        import collections as _collections
        out_tail: "_collections.deque[str]" = _collections.deque(maxlen=60)
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            out_tail.append(line)
            print(f"  {line}", flush=True)
            low = line.lower()
            if "transcrib" in low or "gemini" in low or "words.json" in low or "clip " in low:
                done_clips = min(len(clips), done_clips + 1)
                tracker.update(
                    stage=2,
                    detail=f"Transcribing & scoring clip {done_clips}/{len(clips)}: {line[:70]}",
                    completed=done_clips,
                    total=len(clips),
                    emit_now=False,
                )
            elif "align" in low or "speaker" in low or "plan.edl" in low or "line_map" in low:
                tracker.update(
                    stage=3,
                    detail=f"Matching script lines & building edit plan: {line[:70]}",
                    completed=len(clips),
                    total=len(clips),
                    pct=55,
                    emit_now=False,
                )
            m_edl = re.search(r"(work/[^\s]+/plan\.edl)", line)
            if m_edl:
                plan_path = (ROOT / m_edl.group(1)).resolve()

        rc = proc.wait()
        if rc != 0:
            tracker.stop_heartbeat(final_state="error", final_detail=f"prepare.py exited with code {rc}")
            try:
                import job_archive
                job_archive.event(work_dir, "inspect_failed", exit_code=rc, seconds=round(_time.time() - t_inspect, 1))
            except Exception:
                pass
            _archive_blocked("inspect", f"prepare.py exited with code {rc}\n" + "\n".join(out_tail), script_arg, raw_inputs)
            raise RuntimeError(f"prepare.py failed with exit code {rc}")

        if plan_path is None or not plan_path.is_file():
            cand = work_dir / "plan.edl"
            if cand.is_file():
                plan_path = cand.resolve()
            else:
                edls = sorted(WORK.glob("*/plan.edl"), key=lambda p: p.stat().st_mtime, reverse=True)
                if edls:
                    plan_path = edls[0].resolve()
                    work_dir = plan_path.parent
                    tracker.set_work_dir(work_dir, work_dir.name)

        if plan_path is None or not plan_path.is_file():
            raise FileNotFoundError("Could not locate generated work/<project>/plan.edl")

        tracker.update(
            stage=3,
            detail=f"Measured ({plan_path.relative_to(ROOT)}) — ready for 1-call plan & auto-render.",
            completed=len(clips),
            total=len(clips),
            pct=60,
        )
        tracker.stop_heartbeat(final_state="measured", final_detail="Measured; ready for 1-call plan & auto-render")
        try:   # the measured job goes to the server now, so an edit abandoned before the render is still recorded
            import job_archive
            job_archive.event(plan_path.parent, "inspect_done", seconds=round(_time.time() - t_inspect, 1), clips=len(clips))
            job_archive.send_in_background(plan_path, "inspected")
        except Exception:
            pass
        import cost_control
        cost_control.print_checkpoint(plan_path.parent)
        return plan_path
    except Exception as exc:
        tracker.stop_heartbeat(final_state="error", final_detail=str(exc))
        if not str(exc).startswith("prepare.py failed"):
            _archive_blocked("inspect", f"{type(exc).__name__}: {exc}", script_arg, raw_inputs)
        raise


# ============================================================================
# STAGES 4–5: Execute Render, Premiere .prproj Export & Post-Render QC
# ============================================================================
def order_gate(plan: dict, plan_path: pathlib.Path) -> str:
    """Empty when the plan is safe to render; otherwise the message that stops the render."""
    import script_order

    script = plan.get("script")
    side = plan_path.parent / "plan.json"
    if not script and side.is_file():
        script = common.read_json(side).get("script")
    if not script:
        return "[BLOCKED] This plan names no script, so it cannot be checked. Add #script <path> to plan.edl."
    g = script_order.full_gate(plan, common.resolve(script), plan_path.parent, plan_path)
    try:
        import events

        events.set_project(plan_path.parent.name)
        _n = sum(len(v) for v in (g.get('problems') or {}).values())
        events.emit("check", (f"Plan check blocked: {_n} problem(s)" if g.get('blocked') else "Plan check passed") + f", {len(g.get('tighten') or [])} tight-cut note(s), {len(g.get('style') or [])} style note(s)", ok=not g.get('blocked'), problems=_n)
    except Exception:
        pass
    try:
        import job_report
        job_report.record_plan_check(plan_path.parent, g)
    except Exception:
        pass
    if g.get("voice_note"):
        print(f"[VOICE] {g['voice_note']}. Say so in the report.")
    for t in g.get("tighten") or []:
        print(f"[TIGHT] {t}")
    for t in g.get("style") or []:
        print(f"[STYLE] {t}")
    return script_order.format_gate(g) if g["blocked"] else ""


def finish_job_folder(plan_path: pathlib.Path) -> Optional[pathlib.Path]:
    """Put the last pieces into this edit's folder, next to the videos, the square version, the timeline and the Premiere files:
    the script it was made from and the plan. Returns the folder (None for the old flat layout)."""
    import shutil

    plan = load_edl(plan_path)
    out = pathlib.Path(str(plan.get("output") or ""))
    if not out.name or out.parent == OUTPUT or not out.parent.is_dir():
        return None
    folder = out.parent
    base_stem = common.strip_ratio_prefix(out.stem)
    script = plan.get("script")
    if not script and plan_path.with_name("plan.json").is_file():
        script = common.read_json(plan_path.with_name("plan.json")).get("script")
    if script:
        src = common.resolve(script)
        if src.is_file():
            if src.suffix.lower() in (".txt", ".md"):
                shutil.copy2(src, folder / f"{base_stem}.script.txt")
            else:
                (folder / f"{base_stem}.script.txt").write_text(common.read_script_text(src), encoding="utf-8")
    shutil.copy2(plan_path, folder / f"{base_stem}.plan.edl")
    return folder


def _venv_py_cmd() -> str:
    return r".venv\Scripts\python.exe" if common.IS_WIN else ".venv/bin/python"


def _rel_workspace_path(path: pathlib.Path) -> str:
    """Return a workspace-relative POSIX path when inside ROOT, else the POSIX path string."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _batch_file_list(proj_dir: Path) -> List[str]:
    """Return ordered packet file(s) and clip sheet filename(s) inside `proj_dir`."""
    proj = Path(proj_dir)
    packets: List[str] = []
    manifest_path = proj / "editing_packet.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        if isinstance(manifest, dict):
            raw_parts = manifest.get("parts")
            if isinstance(raw_parts, list):
                for item in raw_parts:
                    cand = ""
                    if isinstance(item, str):
                        cand = item.strip()
                    elif isinstance(item, dict):
                        val = item.get("file") or item.get("path") or item.get("name")
                        if isinstance(val, str):
                            cand = val.strip()
                    if not cand:
                        continue
                    name = cand if (proj / cand).is_file() else Path(cand).name
                    if name and name not in packets:
                        packets.append(name)
    if not packets:
        part_files = sorted(
            (p.name for p in proj.glob("editing_packet_p*.md")),
            key=lambda name: (int(m.group(1)), name) if (m := re.fullmatch(r"editing_packet_p(\d+)\.md", name)) else (10**9, name),
        )
        packets = part_files if part_files else ["editing_packet.md"]
    sheets = sorted(p.name for p in proj.glob("clip_sheet_*.jpg"))
    return [*packets, *sheets]


def _batch_playbook_lines(plan_path: pathlib.Path) -> List[str]:
    """Build workspace-relative Turn 2–5 parallel batch playbook lines for `plan_path`."""
    proj_dir = plan_path.parent
    rel_dir = _rel_workspace_path(proj_dir)
    rel_plan = _rel_workspace_path(plan_path)
    rel_review = f"{rel_dir}/review_sheet.jpg"
    py_cmd = _venv_py_cmd()

    read_paths = [
        name if (name.startswith(rel_dir + "/") or Path(name).is_absolute()) else f"{rel_dir}/{name}"
        for name in _batch_file_list(proj_dir)
    ]
    return [
        f"[BATCH PLAYBOOK] {rel_dir} (1 read batch -> 1 write batch -> 1 execute -> 1 review)",
        f"[TURN 2] Parallel batch READ (all in 1 turn): {', '.join(read_paths)}",
        (
            f"[TURN 3] Parallel batch WRITE (all in 1 turn): "
            f"{rel_dir}/script_analysis.md, {rel_plan} (+ {rel_dir}/decisions.txt if needed)"
        ),
        (
            f"[TURN 4] Single command: {py_cmd} scripts/zeno.py --execute {rel_plan} --square --compact "
            f"(runs plan check + 9:16 & 1:1 render + verify + {rel_review}; "
            "if backgrounded, END TURN and wait for wakeup — NEVER poll manage_task/write_stdin/logs!)"
        ),
        f"[TURN 5] Read {rel_review} ONCE and present deliverables.",
        (
            "[DO NOT] Never read agent/REFERENCE.md, CONCEPT.md, EDIT_RULES.md, FORMATS.md, "
            "scripts/*.py, facts.md, line_map.md, clip_caches.json, ffprobe, or dir."
        ),
    ]


def print_next_step(plan_path: pathlib.Path, fallback_reason: str = "") -> None:
    if fallback_reason:
        clean_reason = " ".join(str(fallback_reason).split())[:220]
        print(
            f"\n[MANUAL_FALLBACK_PACK] Auto-plan unavailable ({clean_reason}); "
            "END TURN if backgrounded; NEVER poll manage_task/logs — follow this batch playbook:",
            flush=True,
        )
    else:
        print("\n[NEXT STEP] Zeno has measured the footage and decided nothing. The Director now:", flush=True)
    for line in _batch_playbook_lines(plan_path):
        print(line, flush=True)


def print_planner_stop(plan_path: pathlib.Path, reason: str, n_calls: int) -> None:
    """Never turn an isolated-planner failure into an unmetered outer-chat retry."""
    clean_reason = " ".join(str(reason).split())[:220]
    print(
        f"\n[PLANNER STOPPED] Automatic planning stopped after {n_calls} completed model call(s) in this invocation "
        f"({clean_reason}). Do NOT plan by hand in this chat, do NOT read the packet, line_map.md, "
        "facts.md or clip sheets, and do NOT run the planner again. Tell the editor the [BLOCKED] lines above, "
        f"exactly as printed. A new chat or inspect does not reset the managed request budget. Saved: {_rel_workspace_path(plan_path)}",
        flush=True,
    )


def _execute_and_finalize(
    plan_path: pathlib.Path,
    square: bool = True,
    loudness: Optional[float] = None,
    gain_db: Optional[float] = None,
    draft: bool = False,
    swap_layout: Optional[str] = None,
    swap_takes: Optional[List[str]] = None,
    error_sfx: Optional[str] = None,
) -> int:
    """Run Stage 4–5 render, QC verification, archive upload, post-job cleanup, and job-folder assembly."""
    p_path = common.resolve(str(plan_path))
    session_gate("render", block=False, project_hint=_rel_workspace_path(p_path))
    apply_stage3_tweaks(p_path, swap_layout=swap_layout, swap_takes=swap_takes, error_sfx=error_sfx)
    import time as _time
    t_render = _time.time()
    rc = run_execute(
        str(p_path),
        square=square,
        loudness=loudness,
        gain_db=gain_db,
        draft=draft,
    )
    if rc != 0:
        try:
            import job_report
            job_report.record_failure(p_path.parent, "render", f"render or verify stopped with code {rc}")
        except Exception:
            pass
        try:   # a failed render is the most useful record of all
            import job_archive
            job_archive.event(p_path.parent, "render_failed", exit_code=rc, seconds=round(_time.time() - t_render, 1))
            job_archive.archive_job(p_path, load_edl(p_path), kind="render_failed", extra={"exit_code": rc})
        except Exception:
            pass
    if rc == 0:
        try:
            import job_archive
            job_archive.event(p_path.parent, "render_done", seconds=round(_time.time() - t_render, 1))
            job_archive.send_in_background(p_path, "render")   # the record again, now with the finished render's events
        except Exception:
            pass
        common.job_done()
        try:
            folder = finish_job_folder(p_path)
            if folder:
                print(f"\n[JOB FOLDER] Everything for this edit is in: {folder}", flush=True)
        except Exception as exc:   # noqa: BLE001  (the videos are already made; this is only tidying)
            print(f"  ! could not gather the script into the job folder: {exc}", flush=True)
    return rc


def run_auto_plan(
    plan_path: pathlib.Path,
    planner: str = "auto",
    auto_render: bool = True,
    square: bool = True,
    loudness: Optional[float] = None,
    gain_db: Optional[float] = None,
    draft: bool = False,
) -> int:
    """Run Zeno's isolated 1-call planner, verify `--check-plan`, print the Pitch Card, and auto-render by default."""
    import cost_control
    import direct_mode

    plan_path = plan_path.resolve()
    work_dir = plan_path.parent
    rel = _rel_workspace_path(plan_path)
    py_cmd = _venv_py_cmd()

    tracker = ProgressTracker(work_dir=work_dir, project_name=work_dir.name, resume=True)
    tracker.start_heartbeat()
    tracker.update(stage=3, detail="Building the editing plan and checking script order and voices", pct=60)

    try:
        if planner == "openai":
            import sol_direct
            used_provider = "openai"
            r = sol_direct.run(work_dir, dry_run=False)
        else:
            used_provider = direct_mode.resolve_provider(planner)
            if used_provider == "codex":
                r = direct_mode.run_codex(work_dir)
            else:
                r = direct_mode.run(work_dir)
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        print(f"[BLOCKED] Isolated planner could not run ({planner}): {reason}", flush=True)
        print_planner_stop(plan_path, reason, 0)
        tracker.stop_heartbeat(final_state="blocked", final_detail=reason)
        return 2

    used_model = r.get("model", used_provider)
    n_calls = len(r.get("calls") or [])
    tot = (r.get("tokens") or {}).get("total", 0)
    secs = r.get("seconds") or round(max(0.0, float(r.get("ended", 0)) - float(r.get("started", 0))), 1)

    if not r.get("passed"):
        reason = str(r.get("error") or r.get("stopped") or "plan check did not pass")
        tracker.stop_heartbeat(final_state="blocked", final_detail=reason)
        print(f"[DIRECT] Auto-plan stopped ({used_provider}/{used_model}, {n_calls} call(s)): {reason}", flush=True)
        print(f"[BLOCKED] Isolated planner did not pass --check-plan: {reason}", flush=True)
        cost_control.print_checkpoint(work_dir)
        try:
            import job_archive
            job_archive.snapshot_plan(plan_path, "check", f"auto-plan blocked: {reason}"[:1500])
            job_archive.event(work_dir, "check", result="blocked", note=f"auto-plan ({used_provider}): {reason}"[:1500])
            job_archive.send_in_background(plan_path, "checking")
        except Exception:
            pass
        print_planner_stop(plan_path, reason, n_calls)
        return 2

    tok_note = f", {tot:,} tokens" if tot else ""
    tracker.update(stage=3, detail="Checking the final plan and preparing the edit", pct=60)
    print(f"[DIRECT] Planned via {used_provider} ({used_model}) in {n_calls} call(s){tok_note} ({secs}s)", flush=True)

    # build_editorial_pitch_card runs order_gate once and prints any [VOICE] / [TIGHT] / [STYLE] lines.
    pitch = build_editorial_pitch_card(work_dir, plan_path)
    if "CRITICAL: [BLOCKED]" in pitch or "CRITICAL: [ORDER BLOCKED]" in pitch:
        msg = order_gate(load_edl(plan_path), plan_path)
        print(msg or "[BLOCKED] Plan check failed after auto-plan.", flush=True)
        cost_control.print_checkpoint(work_dir)
        print_planner_stop(plan_path, "post-plan check failed", n_calls)
        tracker.stop_heartbeat(final_state="blocked", final_detail="Final plan check failed; editor review required")
        return 2

    print("[PLAN PASS] Every line present, once, in script order; no cut inside a word; voices match.", flush=True)
    print(pitch, flush=True)
    notes = " ".join(str(r.get("notes") or "").split())
    if notes:
        print(f"[REVIEW] Director notes: {notes[:260]}{'...' if len(notes) > 260 else ''}", flush=True)
    session_gate("check", block=False, project_hint=str(rel))
    cost_control.print_checkpoint(work_dir)
    try:
        import job_archive
        job_archive.snapshot_plan(plan_path, "check", "PLAN PASS")
        job_archive.event(work_dir, "check", result="passed", note=f"auto-plan ({used_provider}) PLAN PASS")
        job_archive.send_in_background(plan_path, "checking")
    except Exception:
        pass
    do_render = bool(auto_render) and os.environ.get("ZENO_NO_AUTO_RENDER", "").strip() != "1"
    tracker.stop_heartbeat(final_state="plan_ready", final_detail="Plan checked; ready to render")
    if not do_render:
        print(f"[PLAN READY] Plan-only mode requested. To render: {py_cmd} scripts/zeno.py --execute {rel} --square --compact", flush=True)
        return 0
    print("[AUTO-RENDER] Plan verified — rendering 9:16 + 1:1 Square automatically (no Yes/No pause)...", flush=True)
    return _execute_and_finalize(plan_path, square=square, loudness=loudness, gain_db=gain_db, draft=draft)


def run_execute(
    plan_arg: str,
    square: bool = True,
    loudness: Optional[float] = None,
    gain_db: Optional[float] = None,
    draft: bool = False,
) -> int:
    plan_path = common.resolve(plan_arg)
    if not plan_path.is_file():
        raise FileNotFoundError(f"Edit plan not found: {plan_arg}")

    work_dir = plan_path.parent
    plan = load_edl(plan_path)
    segs = plan.get("segments", [])
    total_segs = max(1, len(segs))

    blocked = order_gate(plan, plan_path)
    if blocked:
        print(blocked)
        try:
            import job_archive
            job_archive.snapshot_plan(plan_path, "check", blocked)
            job_archive.event(work_dir, "check", result="blocked", note=blocked[:1500])
        except Exception:
            pass
        print("\n[BLOCKED] Nothing was rendered. The Director fixes plan.edl (see line_map.md), then runs --execute again.")
        return 2

    print("[PLAN PASS] Every line present, once, in script order; no cut inside a word; voices match.", flush=True)
    try:
        import job_archive
        job_archive.snapshot_plan(plan_path, "check", "PLAN PASS")
        job_archive.event(work_dir, "check", result="passed", note="PLAN PASS")
        job_archive.snapshot_plan(plan_path, "render")
        job_archive.event(work_dir, "render_start", square=bool(square), draft=bool(draft))
    except Exception:
        pass

    import script_order

    moved = script_order.snap_cuts(plan)
    if moved:      # mechanical: moves a cut to the quietest point of the gap it already sits in
        plan_path.write_text(edl_parser.format_edl(plan), encoding="utf-8")
        print(f"[SNAP] {moved} cut(s) moved to the quietest point of their gap")

    tracker = ProgressTracker(work_dir=work_dir, project_name=work_dir.name, heartbeat_sec=48.0, resume=True)
    tracker.start_heartbeat()

    try:
        tracker.update(
            stage=4,
            detail=f"Starting segment rendering ({total_segs} segments)...",
            completed=0,
            total=total_segs,
            pct=65,
        )

        py_bin = common.find_venv_python()
        cmd = [
            str(py_bin),
            "-u",
            str(ROOT / "scripts" / "render.py"),
            str(plan_path),
            "--verify",
        ]
        if square:
            cmd.append("--square")
        if draft:
            cmd.append("--draft")
        if loudness is not None:
            cmd.extend(["--loudness", str(loudness)])
        if gain_db is not None:
            cmd.extend(["--gain-db", str(gain_db)])

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("ZENO_MAX_THREADS", str(common.ZENO_MAX_THREADS))

        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        done_segs = 0
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            print(f"  {line}", flush=True)
            low = line.lower()
            if "segment" in low or "cache" in low or re.search(r"\bs\d+[a-z]?\b", low):
                done_segs = min(total_segs, done_segs + 1)
                pct = 65 + int(round(20.0 * (done_segs / total_segs)))
                tracker.update(
                    stage=4,
                    detail=f"Rendering segments ({done_segs}/{total_segs}): {line[:65]}",
                    completed=done_segs,
                    total=total_segs,
                    pct=pct,
                    emit_now=False,
                )
            elif "1:1 square" in low or "premiere" in low or "[verify" in low or "done:" in low:
                tracker.update(
                    stage=5,
                    detail=f"Mastering, Premiere .prproj export & Deep QC: {line[:65]}",
                    completed=total_segs,
                    total=total_segs,
                    pct=92,
                    emit_now=False,
                )

        rc = proc.wait()
        if rc != 0:
            tracker.stop_heartbeat(final_state="error", final_detail=f"render.py exited with code {rc}")
            return rc

        tracker.update(
            stage=5,
            detail="All outputs rendered, Premiere .prproj exported & Deep QC passed!",
            completed=total_segs,
            total=total_segs,
            pct=100,
        )
        tracker.stop_heartbeat(final_state="completed", final_detail="Edit & QC complete")
        return 0
    except Exception as exc:
        tracker.stop_heartbeat(final_state="error", final_detail=str(exc))
        raise


# ============================================================================
# CLI Entrypoint
# ============================================================================
def apply_stage3_tweaks(
    plan_path: Path,
    swap_layout: Optional[str] = None,
    swap_takes: Optional[List[str]] = None,
    error_sfx: Optional[str] = None,
) -> None:
    """Apply Stage 3 quick-tweak overrides (--swap-layout / --swap-take / --error-sfx) directly to plan.edl."""
    if not swap_layout and not swap_takes and not error_sfx:
        return
    plan = edl_parser.load_edl(plan_path)
    changed = False
    if swap_layout:
        plan["preset"] = swap_layout
        for s in plan.get("segments", []):
            if swap_layout == "single":
                s.pop("layout", None)
            elif s.get("layout") in ("split_h", "split_v", "chat_bar", "video_call") or swap_layout in ("split_h", "split_v"):
                s["layout"] = swap_layout
        changed = True
    if swap_takes:
        for item in swap_takes:
            if "=" not in item:
                continue
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


def main() -> None:
    if "--compact" in sys.argv[1:]:
        from compact_run import run
        args = [arg for arg in sys.argv[1:] if arg != "--compact"]
        sys.exit(run([sys.executable, "-u", str(Path(__file__).resolve()), *args]))
    ap = argparse.ArgumentParser(
        description="Zeno — 5-Stage Standalone & Conversational AI Video Editor Harness"
    )
    ap.add_argument(
        "inputs",
        nargs="*",
        help="Raw footage folder or .mp4/.mov clip paths (or plan.edl path when using --execute / --pitch)",
    )
    ap.add_argument("--compact", action="store_true", help="Print concise results; keep full output in work/_logs")
    ap.add_argument("--check", action="store_true", help="Stage 0: Run Edit Bay & System Readiness Check")
    ap.add_argument(
        "--inspect",
        action="store_true",
        help="Stages 1–3: Map clips, transcribe, match script & print the Editorial Pitch Card (stops before rendering)",
    )
    ap.add_argument(
        "--pitch",
        metavar="PLAN_EDL",
        default=None,
        help="Print the Editorial Pitch Card from an existing work/<project>/plan.edl",
    )
    ap.add_argument(
        "--execute",
        "--check-and-execute",
        metavar="PLAN_EDL",
        default=None,
        help="Stages 4–5: Check plan order/cuts/voices, then render MP4, 1:1 Square, Premiere .prproj/.xml & run Deep QC on plan.edl",
    )
    ap.add_argument("--feedback", default=None, metavar="TEXT", help="Record what the editor said about an edit (what was wrong, what changed, what they liked) in the team's job archive")
    ap.add_argument("--plan", default=None, metavar="PLAN_EDL", help="With --feedback: which job (default: the most recent one)")
    ap.add_argument("--feedback-from", default="", help="With --feedback: who said it (default: editor)")
    ap.add_argument("--check-plan", default=None, metavar="PLAN_EDL", help="Check the Director's plan: script order, every line once, cuts outside words, voices. Exit 2 if it would be blocked")
    ap.add_argument("--script", default=None, help="Path to local script .txt or Google Docs URL")
    ap.add_argument("--lang", default="auto", help="Language code or name (e.g. kn, ta, te, ml, hi, en, auto)")
    ap.add_argument("--preset", default="auto", help="Layout preset (auto, single, split_h, split_v, chat_bar, video_call, hybrid)")
    ap.add_argument("--mark-pack", default="flash", help="Mark overlay pack (flash, vignette, glossy, default)")
    ap.add_argument("--square", action="store_true", default=True, help="Export 1:1 Square Meta Ad alongside 9:16 Master (default: True)")
    ap.add_argument("--no-square", action="store_false", dest="square", help="Skip 1:1 Square Meta Ad export")
    ap.add_argument("--loudness", type=float, default=None, help="Target loudness in LUFS (default: -11.5)")
    ap.add_argument("--gain-db", type=float, default=None, help="Additional master gain in dB")
    ap.add_argument("--draft", action="store_true", help="Fast draft encode")
    ap.add_argument(
        "--swap-layout",
        choices=["single", "split_h", "split_v", "chat_bar", "video_call"],
        default=None,
        help="Stage 3 quick-tweak: swap dialogue layout across segments in plan.edl",
    )
    ap.add_argument(
        "--swap-take",
        action="append",
        default=None,
        metavar="SEG_ID=CLIP",
        help="Stage 3 quick-tweak: swap a segment's take (e.g. --swap-take L3=S4.mp4)",
    )
    ap.add_argument(
        "--error-sfx",
        default=None,
        metavar="SFX_FILE",
        help="Swap the Error/Wrong mark SFX file across spec.json and/or plan.edl (e.g. --error-sfx buzzer.wav)",
    )
    ap.add_argument(
        "--remember",
        default=None,
        metavar="RULE_TEXT",
        help="Save a permanent 15-Year Editor rule to styles/micronarrative/brain_memory.json",
    )
    ap.add_argument(
        "--list-memory",
        action="store_true",
        help="List all permanent editorial rules in styles/micronarrative/brain_memory.json",
    )
    ap.add_argument(
        "--sync-learnings",
        action="store_true",
        help="Sync permanent editorial rules (brain_memory.json) with Zeno Cloud",
    )
    ap.add_argument("--login", action="store_true", help="Open browser for Google Sign-In and auto-save Zeno Cloud session token")
    ap.add_argument("--whoami", action="store_true", help="Display current logged-in Zeno Cloud editor session")
    ap.add_argument("--update", action="store_true", help="Run git pull + Zeno Cloud brain memory sync immediately")
    ap.add_argument("--install-schedule", action="store_true", help="(Legacy) removes the old daily background update task; updates now come from the Zeno app popup")
    ap.add_argument("--preflight", action="store_true", help="Run the live pre-job checks (FFmpeg, OpenCV, wav2vec2, Zeno server) and, with --script and inputs, check them too")
    ap.add_argument("--name", default="", help="Notion name for this video; all its files go to Zeno Outputs/<name>/ (reel_<name>.mp4 and SQ_<name>.mp4)")
    ap.add_argument("--style", default="", help="Style this job follows (a folder under styles/, for example micronarrative); written into the plan so the job counts toward that style")
    ap.add_argument("--fresh", action="store_true", help="Rebuild every cached transcript and alignment for these clips (rarely needed: caches refresh themselves when the code changes)")
    ap.add_argument("--skip-preflight", action="store_true", help="Development only: skip the pre-job gate. Agents must never use this.")
    ap.add_argument(
        "--auto-plan",
        action="store_true",
        help="Run Zeno's isolated 1-call subscription planner immediately after inspect (or on an existing work/<project>) and run --check-plan",
    )
    ap.add_argument(
        "--planner",
        choices=("auto", "claude", "codex", "openai"),
        default="auto",
        help="Isolated planner provider for --auto-plan (default: auto)",
    )
    ap.add_argument(
        "--no-auto-render",
        "--plan-only",
        dest="no_auto_render",
        action="store_true",
        help="Stop after --auto-plan without rendering automatically (default: auto-render ON)",
    )
    ap.add_argument("--yes", "-y", action="store_true", help="(Default ON) Auto-render immediately without asking Yes/No")
    args = ap.parse_args()
    if args.fresh:
        os.environ["ZENO_FRESH"] = "1"   # prepare.py runs as a child process and reads this

    if args.install_schedule:
        import auto_updater
        res = auto_updater.remove_daily_scheduler()
        print("  [Auto-Update] Daily schedule removed. Updates now come from the Zeno app popup.")
        if not args.inspect and not args.execute and not args.pitch and not args.check_plan and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.update:
        import auto_updater
        res = auto_updater.ensure_latest_update(force=True, quiet=False)
        sys.exit(0 if res.get("ok") else 1)

    if args.login:
        import auth_session
        ok_login = auth_session.interactive_login()
        sys.exit(0 if ok_login else 1)

    if args.whoami:
        import auth_session
        info = auth_session.get_session_info()
        if info.get("authenticated"):
            who = info.get("email") or info.get("name") or info.get("mode")
            print(f"  [ZENO CLOUD] Signed in as: {who} (source={info.get('source')}) -> {info.get('cloud_url')}")
        else:
            print("  [ZENO CLOUD] Not signed in. Run: python3 scripts/zeno.py --login")
        if not args.inspect and not args.execute and not args.pitch and not args.check_plan and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.sync_learnings:
        import brain_memory
        res = brain_memory.sync_brain_memory_with_cloud("micronarrative")
        print(f"  [BRAIN MEMORY SYNC] {res.get('message')}")
        import format_library
        print(f"  [FORMAT LIBRARY SYNC] {format_library.sync_team()['message']}")
        if not args.inspect and not args.execute and not args.pitch and not args.check_plan and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.remember:
        import brain_memory
        entry = brain_memory.add_brain_memory_rule(args.remember)
        print(f"  [BRAIN MEMORY SAVED] [{entry['id']}] {entry['rule']}")
        try:
            brain_memory.sync_brain_memory_with_cloud("micronarrative")
        except Exception:
            pass
        if not args.inspect and not args.execute and not args.pitch and not args.check_plan and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.list_memory:
        import brain_memory
        for line in brain_memory.format_brain_memory_for_brief("micronarrative"):
            print(line)
        if not args.inspect and not args.execute and not args.pitch and not args.check_plan and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.error_sfx and not args.pitch and not args.execute:
        spec_p = ROOT / "styles" / "micronarrative" / "spec.json"
        if spec_p.is_file():
            spec_data = json.loads(spec_p.read_text(encoding="utf-8"))
            spec_data.setdefault("tuning", {})["wrong_sfx_primary"] = args.error_sfx
            spec_p.write_text(json.dumps(spec_data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"  [SFX UPDATED] styles/micronarrative/spec.json -> wrong_sfx_primary = {args.error_sfx}")
        if not args.inspect and not args.auto_plan and not args.inputs:
            sys.exit(0)

    if args.inspect or (args.inputs and args.script):
        common.job_begin(identity=json.dumps([args.script, args.name, args.inputs], ensure_ascii=False))
    if args.preflight:
        import preflight

        common.job_start_epoch()
        session_gate("preflight", block=True, script_arg=args.script or "", inputs=args.inputs)
        print("\nZeno pre-job checks (live)")
        checks = preflight.run_system_checks()
        preflight.print_checks(checks)
        passed = preflight.save_state(checks)
        print("\nSYSTEM: " + ("ALL CHECKS PASSED. Ask the user for the script link and the footage, then run --inspect." if passed else "NOT READY. Fix the FAIL lines above and run --preflight again."))
        if passed and args.script and args.inputs:
            res = preflight.validate_inputs(resolve_script_input(args.script), args.inputs)
            print("\nYour inputs")
            preflight.print_inputs_report(res)
            passed = not res["errors"]
        sys.exit(0 if passed else 1)

    if args.check and not args.inspect and not args.execute and not args.pitch and not args.auto_plan and not args.inputs:
        try:
            import auto_updater
            auto_updater.ensure_latest_update(force=False, min_interval_hours=12.0, quiet=False)
        except Exception:
            pass
        ok, _ = check_system_readiness(quiet=False)
        sys.exit(0 if ok else 1)

    if getattr(args, "feedback", None):
        # What the editor said about an edit (what was wrong, what they changed, what they liked) goes into its archive record.
        import job_archive
        if getattr(args, "plan", None):
            fb_plan = common.resolve(args.plan)
        else:
            plans = sorted((common.ROOT / "work").glob("*/plan.edl"), key=lambda q: q.stat().st_mtime)
            fb_plan = plans[-1] if plans else None
        if fb_plan is None or not Path(fb_plan).is_file():
            print("[FEEDBACK] no job found; pass --plan work/<project>/plan.edl")
            sys.exit(2)
        job_archive.add_feedback(Path(fb_plan).parent, args.feedback, source=getattr(args, "feedback_from", "") or "editor")
        job_archive.event(Path(fb_plan).parent, "feedback", text=args.feedback[:500])
        try:
            stored = job_archive.archive_job(Path(fb_plan), load_edl(Path(fb_plan)), kind="feedback")
        except Exception:
            stored = False
        print(f"[FEEDBACK] noted for {Path(fb_plan).parent.name}" + ("" if stored else " (kept on this computer; it goes to the server with the next job)"))
        sys.exit(0)

    if args.check_plan:
        p_path = common.resolve(args.check_plan)
        msg = order_gate(load_edl(p_path), p_path)
        print(msg or "[PLAN PASS] Every line present, once, in script order; no cut inside a word; voices match.")
        session_gate("check", block=False, project_hint=str(p_path.relative_to(ROOT)) if str(p_path).startswith(str(ROOT)) else str(p_path))
        import cost_control
        cost_control.print_checkpoint(p_path.parent)
        try:   # the job archive keeps every version of the plan and what the check said about it
            import job_archive
            job_archive.snapshot_plan(p_path, "check", msg or "PLAN PASS")
            job_archive.event(p_path.parent, "check", result="blocked" if msg else "passed", note=(msg or "")[:1500])
            job_archive.send_in_background(p_path, "checking")
        except Exception:
            pass
        sys.exit(2 if msg else 0)

    if args.pitch:
        p_path = common.resolve(args.pitch)
        apply_stage3_tweaks(p_path, swap_layout=args.swap_layout, swap_takes=args.swap_take, error_sfx=args.error_sfx)
        print(build_editorial_pitch_card(p_path.parent, p_path))
        sys.exit(0)

    if args.execute:
        p_path = common.resolve(args.execute)
        rc = _execute_and_finalize(
            p_path,
            square=args.square,
            loudness=args.loudness,
            gain_db=args.gain_db,
            draft=args.draft,
            swap_layout=args.swap_layout,
            swap_takes=args.swap_take,
            error_sfx=args.error_sfx,
        )
        sys.exit(rc)

    if args.inspect:
        if not args.inputs or not args.script:
            ap.error("--inspect requires <raw_dir_or_clips> and --script <path_or_gdoc_url>")
        job_gate(args.inputs, args.script, skip=args.skip_preflight, style=args.style, isolated_planner=bool(args.auto_plan))
        plan_path = run_inspect(
            raw_inputs=args.inputs,
            script_arg=args.script,
            lang=args.lang,
            preset=args.preset,
            mark_pack=args.mark_pack,
            style=args.style,
            name=args.name,
        )
        if args.auto_plan:
            sys.exit(
                run_auto_plan(
                    plan_path,
                    planner=args.planner,
                    auto_render=not args.no_auto_render,
                    square=args.square,
                    loudness=args.loudness,
                    gain_db=args.gain_db,
                    draft=args.draft,
                )
            )
        print_next_step(plan_path)
        sys.exit(0)

    if args.inputs and args.script:     # same as --inspect: without a Director there is no plan to render
        job_gate(args.inputs, args.script, skip=args.skip_preflight, style=args.style, isolated_planner=bool(args.auto_plan))
        plan_path = run_inspect(
            raw_inputs=args.inputs,
            script_arg=args.script,
            lang=args.lang,
            preset=args.preset,
            mark_pack=args.mark_pack,
            style=args.style,
            name=args.name,
        )
        if args.auto_plan:
            sys.exit(
                run_auto_plan(
                    plan_path,
                    planner=args.planner,
                    auto_render=not args.no_auto_render,
                    square=args.square,
                    loudness=args.loudness,
                    gain_db=args.gain_db,
                    draft=args.draft,
                )
            )
        print_next_step(plan_path)
        sys.exit(0)

    if args.auto_plan:
        target = args.plan or (args.inputs[0] if args.inputs else "")
        if not target:
            ap.error("--auto-plan requires --inspect <footage> --script <script>, or --plan work/<project>/plan.edl (or positional work/<project>)")
        cand = common.resolve(target)
        plan_path = (cand / "plan.edl") if cand.is_dir() else cand
        if not plan_path.is_file():
            print(f"[BLOCKED] Edit plan not found: {target} (run zeno.py --inspect first)", flush=True)
            sys.exit(2)
        sys.exit(
            run_auto_plan(
                plan_path,
                planner=args.planner,
                auto_render=not args.no_auto_render,
                square=args.square,
                loudness=args.loudness,
                gain_db=args.gain_db,
                draft=args.draft,
            )
        )

    ap.print_help()


if __name__ == "__main__":
    main()
