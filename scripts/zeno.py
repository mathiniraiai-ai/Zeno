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


def job_gate(raw_inputs: List[str], script_arg: str, skip: bool = False) -> None:
    """Nothing starts until the live checks pass and the script and footage the user gave have been checked."""
    import preflight

    if skip:
        print("\n[WARN] --skip-preflight: live checks and input validation skipped (development only).")
        return
    print("\n== Pre-job gate: live checks ==")
    checks = preflight.run_system_checks()
    preflight.print_checks(checks)
    if not preflight.save_state(checks):
        print("\n[BLOCKED] A live check failed, so the edit did not start. Fix the FAIL lines above and run again.")
        sys.exit(2)
    print("\n== Pre-job gate: your script and footage ==")
    try:
        script_path = resolve_script_input(script_arg)
    except Exception as exc:
        print(f"  [FAIL] script: {exc}")
        print("\n[BLOCKED] The script could not be loaded, so the edit did not start.")
        sys.exit(2)
    res = preflight.validate_inputs(script_path, raw_inputs)
    preflight.print_inputs_report(res)
    if res["errors"]:
        print("\n[BLOCKED] The script or footage has a problem, so the edit did not start. Fix the FAIL lines and run again.")
        sys.exit(2)
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
    `work/<project>/brief.txt` and `work/<project>/plan.edl`.
    """
    work_dir = pathlib.Path(work_dir).resolve()
    plan_path = pathlib.Path(plan_path).resolve()
    plan = load_edl(plan_path)
    brief_path = work_dir / "brief.txt"
    brief_txt = brief_path.read_text(encoding="utf-8", errors="ignore") if brief_path.is_file() else ""

    segs = plan.get("segments", [])
    marks = list(plan.get("marks", []) or [])
    for s in segs:
        marks.extend(s.get("marks", []) or [])

    total_dur = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segs)
    mm = int(total_dur) // 60
    ss = total_dur - mm * 60

    # Detect Concept & Style
    has_banners = any(s.get("banners") or s.get("stop_saying") for s in segs) or ("stop saying" in brief_txt.lower())
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

    # Extract Take Scoring & Warnings from brief.txt
    best_takes = re.findall(r"\[BEST_TAKE\][^\n]*", brief_txt)
    alt_takes = re.findall(r"\[ALT_TAKE\][^\n]*", brief_txt)
    warnings = []
    if "[WRONG_SPEAKER_NO_FIX]" in brief_txt:
        warnings.append("CRITICAL: [WRONG_SPEAKER_NO_FIX] detected in brief.txt — a line is missing the right speaker's voice.")
    if "[SPEAKER_CHECK] not applied" in brief_txt:
        warnings.append("NOTE: Speaker voices were similar; verify speaker assignment on split-screen turns.")

    # Summarize Sections
    hook_segs = [s for s in segs if str(s.get("layout", "single")) == "single"]
    split_segs = [s for s in segs if str(s.get("layout", "")) in ("split_h", "split_v", "video_call", "chat_bar")]
    hook_dur = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in hook_segs)
    split_dur = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in split_segs)

    right_count = sum(1 for m in marks if str(m.get("type") or m.get("kind") or "").lower() in ("right", "check"))
    wrong_count = sum(1 for m in marks if str(m.get("type") or m.get("kind") or "").lower() in ("wrong", "x", "cross", "error"))

    out_mp4 = pathlib.Path(str(plan.get("output") or (OUTPUT / f"{work_dir.name}.mp4")))
    out_sq = out_mp4.with_name(f"{out_mp4.stem}_1x1.mp4")
    out_prproj = out_mp4.with_suffix(".prproj")
    out_xml = out_mp4.with_suffix(".premiere.xml")

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
    if best_takes or alt_takes:
        card_lines.append(f"  • Take Selection    : {len(best_takes)} [BEST_TAKE] kept, {len(alt_takes)} [ALT_TAKE] pruned")
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
    project_name = script_path.stem
    work_dir = WORK / project_name

    tracker = ProgressTracker(work_dir=work_dir, project_name=project_name, heartbeat_sec=48.0)
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
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
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
            elif "align" in low or "speaker" in low or "plan.edl" in low or "brief.txt" in low:
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
            detail=f"Editorial Pitch ready ({plan_path.relative_to(ROOT)}) — waiting for approval to render.",
            completed=len(clips),
            total=len(clips),
            pct=60,
        )
        tracker.stop_heartbeat(final_state="awaiting_confirmation", final_detail="Stage 3 Pitch Card ready")

        print()
        print(build_editorial_pitch_card(plan_path.parent, plan_path))
        return plan_path
    except Exception as exc:
        tracker.stop_heartbeat(final_state="error", final_detail=str(exc))
        raise


# ============================================================================
# STAGES 4–5: Execute Render, Premiere .prproj Export & Post-Render QC
# ============================================================================
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

    tracker = ProgressTracker(work_dir=work_dir, project_name=work_dir.name, heartbeat_sec=48.0)
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
    ap = argparse.ArgumentParser(
        description="Zeno — 5-Stage Standalone & Conversational AI Video Editor Harness"
    )
    ap.add_argument(
        "inputs",
        nargs="*",
        help="Raw footage folder or .mp4/.mov clip paths (or plan.edl path when using --execute / --pitch)",
    )
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
        metavar="PLAN_EDL",
        default=None,
        help="Stages 4–5: Render MP4, 1:1 Square, Premiere .prproj/.xml & run Deep QC on plan.edl",
    )
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
    ap.add_argument("--install-schedule", action="store_true", help="Install daily 11:00 AM background auto-update schedule")
    ap.add_argument("--preflight", action="store_true", help="Run the live pre-job checks (FFmpeg, OpenCV, wav2vec2, Zeno server) and, with --script and inputs, check them too")
    ap.add_argument("--fresh", action="store_true", help="Rebuild every cached transcript and alignment for these clips (rarely needed: caches refresh themselves when the code changes)")
    ap.add_argument("--skip-preflight", action="store_true", help="Development only: skip the pre-job gate. Agents must never use this.")
    ap.add_argument("--yes", "-y", action="store_true", help="Auto-confirm the Stage 3 Yes/No gate in standalone mode")
    args = ap.parse_args()
    if args.fresh:
        os.environ["ZENO_FRESH"] = "1"   # prepare.py runs as a child process and reads this

    if args.install_schedule:
        import auto_updater
        res = auto_updater.install_daily_scheduler(hour=11, minute=0)
        print(f"  [Auto-Update] ✓ Daily 11:00 AM auto-updater configured ({res.get('schedule', 'active')})")
        if not args.inspect and not args.execute and not args.pitch and not args.inputs:
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
        if not args.inspect and not args.execute and not args.pitch and not args.inputs:
            sys.exit(0)

    if args.sync_learnings:
        import brain_memory
        res = brain_memory.sync_brain_memory_with_cloud("micronarrative")
        print(f"  [BRAIN MEMORY SYNC] {res.get('message')}")
        import format_library
        print(f"  [FORMAT LIBRARY SYNC] {format_library.sync_team()['message']}")
        if not args.inspect and not args.execute and not args.pitch and not args.inputs:
            sys.exit(0)

    if args.remember:
        import brain_memory
        entry = brain_memory.add_brain_memory_rule(args.remember)
        print(f"  [BRAIN MEMORY SAVED] [{entry['id']}] {entry['rule']}")
        try:
            brain_memory.sync_brain_memory_with_cloud("micronarrative")
        except Exception:
            pass
        if not args.inspect and not args.execute and not args.pitch and not args.inputs:
            sys.exit(0)

    if args.list_memory:
        import brain_memory
        for line in brain_memory.format_brain_memory_for_brief("micronarrative"):
            print(line)
        if not args.inspect and not args.execute and not args.pitch and not args.inputs:
            sys.exit(0)

    if args.error_sfx and not args.pitch and not args.execute:
        spec_p = ROOT / "styles" / "micronarrative" / "spec.json"
        if spec_p.is_file():
            spec_data = json.loads(spec_p.read_text(encoding="utf-8"))
            spec_data.setdefault("tuning", {})["wrong_sfx_primary"] = args.error_sfx
            spec_p.write_text(json.dumps(spec_data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"  [SFX UPDATED] styles/micronarrative/spec.json -> wrong_sfx_primary = {args.error_sfx}")
        if not args.inspect and not args.inputs:
            sys.exit(0)

    if args.preflight:
        import preflight

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

    if args.check and not args.inspect and not args.execute and not args.pitch and not args.inputs:
        try:
            import auto_updater
            auto_updater.ensure_latest_update(force=False, min_interval_hours=12.0, quiet=False)
        except Exception:
            pass
        ok, _ = check_system_readiness(quiet=False)
        sys.exit(0 if ok else 1)

    if args.pitch:
        p_path = common.resolve(args.pitch)
        apply_stage3_tweaks(p_path, swap_layout=args.swap_layout, swap_takes=args.swap_take, error_sfx=args.error_sfx)
        print(build_editorial_pitch_card(p_path.parent, p_path))
        sys.exit(0)

    if args.execute:
        p_path = common.resolve(args.execute)
        apply_stage3_tweaks(p_path, swap_layout=args.swap_layout, swap_takes=args.swap_take, error_sfx=args.error_sfx)
        rc = run_execute(
            args.execute,
            square=args.square,
            loudness=args.loudness,
            gain_db=args.gain_db,
            draft=args.draft,
        )
        sys.exit(rc)

    if args.inspect:
        if not args.inputs or not args.script:
            ap.error("--inspect requires <raw_dir_or_clips> and --script <path_or_gdoc_url>")
        job_gate(args.inputs, args.script, skip=args.skip_preflight)
        plan_path = run_inspect(
            raw_inputs=args.inputs,
            script_arg=args.script,
            lang=args.lang,
            preset=args.preset,
            mark_pack=args.mark_pack,
        )
        print(f"\n[NEXT STEP] Review the pitch above. Once approved, run:")
        print(f"  python3 scripts/zeno.py --execute {plan_path.relative_to(ROOT)} --square")
        sys.exit(0)

    # Interactive Standalone Mode (Stage 0 -> Stage 1–3 -> [Y/n] -> Stage 4–5)
    if args.inputs and args.script:
        job_gate(args.inputs, args.script, skip=args.skip_preflight)
        plan_path = run_inspect(
            raw_inputs=args.inputs,
            script_arg=args.script,
            lang=args.lang,
            preset=args.preset,
            mark_pack=args.mark_pack,
        )
        if not args.yes:
            if not sys.stdin.isatty():
                print("\n[INFO] Non-interactive session detected; stopping at Stage 3 confirmation gate.")
                print(f"To execute Stage 4–5 render, run: python3 scripts/zeno.py --execute {plan_path.relative_to(ROOT)}")
                sys.exit(0)
            ans = input("\n👉 Start editing & rendering with this plan? [Y/n]: ").strip().lower()
            if ans not in ("", "y", "yes"):
                print(f"\n[PAUSED] Edit plan saved at {plan_path.relative_to(ROOT)}.")
                print(f"Adjust {plan_path.relative_to(ROOT)} if needed, then run:")
                print(f"  python3 scripts/zeno.py --execute {plan_path.relative_to(ROOT)}")
                sys.exit(0)

        rc = run_execute(
            str(plan_path),
            square=args.square,
            loudness=args.loudness,
            gain_db=args.gain_db,
            draft=args.draft,
        )
        sys.exit(rc)

    # Default if invoked with no arguments: run readiness check + usage hint
    check_system_readiness(quiet=False)
    ap.print_help()


if __name__ == "__main__":
    main()
