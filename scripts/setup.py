#!/usr/bin/env python3
"""One-command cross-platform bootstrap & health check for Zeno (macOS & Windows).

Usage:
    python3 scripts/setup.py              # Verify FFmpeg, venv, wav2vec2 cache, and all asset packs
    python3 scripts/setup.py --init-venv  # Create local .venv and install ML/audio dependencies
    python3 scripts/setup.py --prewarm    # Pre-download torchaudio wav2vec2 checkpoint
    python3 scripts/setup.py --smoke      # Run real checks (FFmpeg filters, wav2vec2, auth)
    python3 scripts/setup.py --login      # Open browser for Google Sign-In and save session
    python3 scripts/setup.py --whoami     # Check active Zeno Cloud / Gemini auth status
    python3 scripts/setup.py --logout     # Clear saved local Zeno Cloud session token
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


REQUIRED_ASSETS = {
    "Face Detection ONNX (YuNet)": common.YUNET_MODEL,
    "Font (Montserrat-ExtraBold)": common.FONTS_DIR / "Montserrat-ExtraBold.ttf",
    "Overlay (video_call_bar)": common.OVERLAYS_DIR / "video_call_bar.png",
    "Overlay (split_border)": common.OVERLAYS_DIR / "split_border.png",
    "Overlay (nova_phone)": common.OVERLAYS_DIR / "nova_phone.png",
    "Premiere Base (base_1080x1920)": common.PREMIERE_DIR / "base_1080x1920.prproj.gz",
    "Template (mark_packs.json)": common.TEMPLATES_DIR / "mark_packs.json",
    "Template (supernova_layouts.json)": common.TEMPLATES_DIR / "supernova_layouts.json",
    "Template (video_call_pip.json)": common.TEMPLATES_DIR / "video_call_pip.json",
    "Template (stop_saying_banners.json)": common.TEMPLATES_DIR / "stop_saying_banners.json",
    "Template (caption_pill.json)": common.TEMPLATES_DIR / "caption_pill.json",
    "Transition (warm_flash.json)": common.TRANSITIONS_DIR / "warm_flash.json",
    "SFX Library Map": common.SFX_DIR / "sfx_library_map.json",
    "SFX Notification (mp3)": common.SFX_DIR / "zeno_notification.mp3",
    "SFX Notification (wav)": common.SFX_DIR / "zeno_notification.wav",
    "UI App Icon (zeno_icon.png)": common.ASSETS / "ui" / "zeno_icon.png",
    "UI Toast (zeno_toast.html)": common.ASSETS / "ui" / "zeno_toast.html",
}


def check_or_setup_ffmpeg() -> bool:
    print("\n[1/5] Checking FFmpeg & FFprobe...")
    bundled_dir = common.ROOT / "tools" / "ffmpeg"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    target_bin = bundled_dir / ("ffmpeg.exe" if common.IS_WIN else "ffmpeg")
    target_probe = bundled_dir / ("ffprobe.exe" if common.IS_WIN else "ffprobe")

    ffmpeg_path = common.resolve_ffmpeg()
    ffprobe_path = common.resolve_ffprobe()
    try:
        r = subprocess.run([str(ffmpeg_path), "-version"], capture_output=True, text=True, timeout=10)
        first_line = (r.stdout or r.stderr or "").splitlines()[0] if (r.stdout or r.stderr) else "OK"
        print(f"  [OK] FFmpeg  : {ffmpeg_path}")
        print(f"       Version : {first_line}")
        print(f"  [OK] FFprobe : {ffprobe_path} ({'exists' if ffprobe_path.exists() else 'fallback via ffmpeg -i'})")
        return r.returncode == 0
    except Exception as exc:
        print(f"  [!] FFmpeg not runnable at {ffmpeg_path}: {exc}")
        if common.IS_MAC:
            print("      Install via Homebrew: brew install ffmpeg")
        elif common.IS_WIN:
            print("      Install via Winget  : winget install Gyan.FFmpeg")
        return False


def check_or_setup_venv(init_venv: bool = False, prewarm: bool = False) -> bool:
    print("\n[2/5] Checking Python Virtual Environment & ML Packages...")
    if init_venv:
        if not (common.ROOT / ".venv").exists():
            print("  + Creating local virtual environment at .venv ...")
            subprocess.run([sys.executable, "-m", "venv", str(common.ROOT / ".venv")], check=True)
        venv_py = common.find_venv_python()
        gpu_installer = Path(__file__).resolve().parent / "gpu_install.py"
        if gpu_installer.is_file():
            print(f"  + Installing Zeno dependencies via gpu_install.py into {venv_py} ...")
            subprocess.run([str(venv_py), "-B", str(gpu_installer)], check=True)
        else:
            req_file = common.ROOT / "requirements.txt"
            print(f"  + Installing requirements into {venv_py} ...")
            subprocess.run([str(venv_py), "-m", "pip", "install", "-r", str(req_file)], check=True)

    venv_py = common.find_venv_python()
    print(f"  [OK] Venv Python: {venv_py}")

    required_mods = [
        "torch",
        "torchaudio",
        "cv2",
        "numpy",
        "soundfile",
        "PIL",
        "librosa",
        "google.genai",
        "httpx",
        "certifi",
        "dotenv",
    ]
    optional_mods = ["silero_vad"]
    check_code = (
        "import importlib, json\n"
        f"mods = {required_mods + optional_mods!r}\n"
        "status = {}\n"
        "for m in mods:\n"
        "    try:\n"
        "        mod = importlib.import_module(m)\n"
        "        status[m] = getattr(mod, '__version__', 'installed')\n"
        "    except Exception:\n"
        "        status[m] = None\n"
        "print(json.dumps(status))\n"
    )
    pkgs_ok = True
    try:
        r = subprocess.run([str(venv_py), "-B", "-c", check_code], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            pkg_status = json.loads(r.stdout.strip().splitlines()[-1])
            found = [f"{k} ({v})" for k, v in pkg_status.items() if v]
            missing_req = [k for k in required_mods if not pkg_status.get(k)]
            missing_opt = [k for k in optional_mods if not pkg_status.get(k)]
            print(f"  [OK] Installed  : {', '.join(found)}")
            if missing_req:
                print(f"  [FAIL] Missing required packages in {venv_py}: {', '.join(missing_req)}")
                pkgs_ok = False
            if missing_opt:
                print(f"  [!] Missing optional packages in {venv_py}: {', '.join(missing_opt)}")
        else:
            print(f"  [!] Could not inspect packages in {venv_py}: {(r.stderr or '').strip()[:200]}")
            pkgs_ok = False
    except Exception as exc:
        print(f"  [!] Could not inspect packages in {venv_py}: {exc}")
        pkgs_ok = False

    print("\n[3/5] Checking Meta wav2vec2 Alignment Checkpoint Cache...")
    ckpt_dir = common.MODELS_DIR / "torch" / "hub" / "checkpoints"
    wav2vec_ckpts = list(ckpt_dir.glob("*wav2vec2*")) if ckpt_dir.is_dir() else []
    if wav2vec_ckpts:
        for ckpt in wav2vec_ckpts:
            mb = ckpt.stat().st_size / (1024 * 1024)
            print(f"  [OK] Cached checkpoint: {ckpt} ({mb:.1f} MB)")
    elif prewarm:
        print("  + Pre-warming torchaudio wav2vec2 checkpoint (~360 MB)...")
        prewarm_code = (
            "import sys; sys.path.insert(0, r'%s'); "
            "from align_engine.acoustic import AcousticModel; "
            "AcousticModel()._ensure_model_loaded()" % Path(__file__).resolve().parent
        )
        r = subprocess.run([str(venv_py), "-B", "-c", prewarm_code], check=False)
        if r.returncode != 0:
            print("  [!] wav2vec2 pre-warm failed; verify network/SSL and retry with --prewarm.")
            return False
    else:
        print(f"  [i] wav2vec2 checkpoint not yet cached in {ckpt_dir} (will auto-download on first CTC alignment or run with --prewarm).")

    return pkgs_ok


def verify_assets() -> bool:
    print("\n[4/5] Verifying Reusable Assets, Swappable Mark Packs & SFX Packs...")
    all_ok = True
    for style in ("default", "glossy", "flash", "vignette"):
        r_path = common.resolve_mark_asset("right", style=style)
        w_path = common.resolve_mark_asset("wrong", style=style)
        ok = r_path.is_file() and w_path.is_file()
        all_ok = all_ok and ok
        status = "OK" if ok else "MISSING"
        print(f"  [{status}] Mark Pack '{style:<8}' -> right={r_path.name}, wrong={w_path.name}")

    for pack in ("default", "supernova", "kn20228", "w42"):
        r_sfx = common.resolve_sfx_asset("right", pack=pack)
        w_sfx = common.resolve_sfx_asset("wrong", pack=pack)
        pack_files = common.SFX_PACKS.get(pack, {}).values()
        pack_complete = all((common.SFX_DIR / fn).is_file() for fn in pack_files)
        ok = r_sfx.is_file() and w_sfx.is_file() and pack_complete
        all_ok = all_ok and ok
        status = "OK" if ok else "MISSING"
        print(f"  [{status}] SFX Pack  '{pack:<8}' -> right={r_sfx.name}, wrong={w_sfx.name}")

    for label, path in REQUIRED_ASSETS.items():
        exists = path.is_file()
        all_ok = all_ok and exists
        status = "OK" if exists else "MISSING"
        try:
            rel_disp = path.relative_to(common.ROOT)
        except ValueError:
            rel_disp = path
        print(f"  [{status}] {label:<38}: {rel_disp}")

    try:
        import subtitles as _subtitles
        fonts_ok = _subtitles.ensure_windows_user_fonts(quiet=False)
        all_ok = all_ok and fonts_ok
    except Exception as exc:
        print(f"  [WARN] Windows user font registration skipped: {exc}")

    return all_ok


def check_env() -> None:
    print("\n[5/5] Checking Environment & API Configuration...")
    g = common.gpu_report()
    print(f"  [OK] Processing: aligner on {g['device'].upper()}{' (' + g['name'] + ')' if g.get('name') else ''}, video encoder {g['encoder'] or 'CPU (libx264)'}")
    if g.get("hint"):
        print(f"       Hint: {g['hint']}")

    common.load_dotenv()
    import auth_session
    info = auth_session.get_session_info()
    local_env = common.ROOT / ".env"
    session_file = common.ROOT / ".zeno_session.json"
    if session_file.is_file():
        print(f"  [OK] Found active Zeno Cloud session ({session_file.name}: {info.get('email') or info.get('name') or 'authenticated'})")
    elif local_env.is_file():
        print(f"  [OK] Found local .env ({local_env})")
    else:
        print("  [i] No session or .env found yet. Run: python3 scripts/setup.py --login")

    if info.get("mode") == "zeno_cloud":
        print(f"  [OK] Transcription configured: Zeno Cloud session ({info.get('source')})")
    elif info.get("mode") == "gemini_direct":
        print("  [OK] Transcription configured: Direct GEMINI_API_KEY")
    else:
        print("  [i] Transcription configured: No (run: python3 scripts/setup.py --login)")


def smoke_test() -> bool:
    """Prove the machine can really edit: FFmpeg filters Zeno uses, the wav2vec2 aligner, and the Gemini key setting."""
    print("\n[smoke] Running real checks (about 1-2 minutes the first time)...")
    ok = True
    ffmpeg_path = common.resolve_ffmpeg()

    test_graph = (
        "tpad=stop=2:stop_mode=clone,"
        "lutrgb=r='val+(255-val)*100/255':enable='between(t,0,0.2)',"
        "drawbox=x=0:y=0:w=4:h=64:color=white@0.8:t=fill,format=yuv420p"
    )
    r = subprocess.run([str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.5:r=24",
                        "-vf", test_graph, "-f", "null", "-"], capture_output=True, text=True)
    print(f"  [{'OK' if r.returncode == 0 else 'FAIL'}] FFmpeg filters (tpad, lutrgb, drawbox)" + ("" if r.returncode == 0 else f": {r.stderr.strip()[:200]}"))
    ok = ok and r.returncode == 0

    loud = subprocess.run([str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1",
                           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-f", "null", "-"], capture_output=True, text=True)
    print(f"  [{'OK' if loud.returncode == 0 else 'FAIL'}] FFmpeg loudnorm")
    ok = ok and loud.returncode == 0

    venv_py = common.find_venv_python()
    code = (
        "import sys, time, os, tempfile\n"
        f"sys.path.insert(0, r'{Path(__file__).resolve().parent}')\n"
        "import numpy as np, soundfile as sf\n"
        "from align_engine.acoustic import AcousticModel\n"
        "t = time.time(); m = AcousticModel()\n"
        "fd, p = tempfile.mkstemp(prefix='zeno_smoke_', suffix='.wav'); os.close(fd)\n"
        "try:\n"
        "    sf.write(p, np.zeros(16000, dtype='float32'), 16000)\n"
        "    lp, _, dur, tpf = m.extract_emissions(p)\n"
        "finally:\n"
        "    if os.path.exists(p): os.unlink(p)\n"
        "print('OK device=%s frames=%d seconds=%.1f' % (m.device, lp.shape[0], time.time() - t))\n"
    )
    r = subprocess.run([str(venv_py), "-B", "-c", code], capture_output=True, text=True)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    good = r.returncode == 0 and line.startswith("OK")
    print(f"  [{'OK' if good else 'FAIL'}] wav2vec2 alignment model: {line if good else (r.stderr.strip()[-300:] or 'no output')}")
    ok = ok and good

    common.load_dotenv()
    import auth_session
    info = auth_session.get_session_info()
    has_key = bool(info.get("authenticated"))
    if info.get("mode") == "zeno_cloud":
        who = info.get("email") or info.get("name") or "team token"
        detail = f"Zeno Cloud session active ({who}, source={info.get('source')})"
    elif info.get("mode") == "gemini_direct":
        detail = "Gemini key set in .env (not called)"
    else:
        detail = "not signed in yet. Run: python3 scripts/setup.py --login"
    print(f"  [{'OK' if has_key else 'FAIL'}] Transcription access: {detail}")
    ok = ok and has_key

    try:
        import subtitles as _subtitles
        _subtitles.ensure_windows_user_fonts(quiet=True)
        if _subtitles.available():
            probe_ok, probe_detail = _subtitles.verify_indic_render_probe()
            print(f"  [{'OK' if probe_ok else 'FAIL'}] Indic font render probe: {probe_detail}")
            ok = ok and probe_ok
        else:
            print("  [i] Indic font render probe skipped (Node subtitle renderer not installed)")
    except Exception as exc:
        print(f"  [FAIL] Indic font render probe error: {exc}")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(description="Bootstrap and verify Zeno cross-platform setup.")
    ap.add_argument("--init-venv", action="store_true", help="Create local .venv and install dependencies")
    ap.add_argument("--prewarm", action="store_true", help="Pre-download wav2vec2 checkpoint into models/torch")
    ap.add_argument("--smoke", action="store_true", help="Run real checks: FFmpeg filters, wav2vec2 forward pass, auth")
    ap.add_argument("--login", action="store_true", help="Open browser for Google Sign-In and auto-save session token")
    ap.add_argument("--whoami", action="store_true", help="Check active Zeno Cloud / Gemini authentication status")
    ap.add_argument("--logout", action="store_true", help="Clear saved local Zeno Cloud session token")
    args = ap.parse_args()

    common.load_dotenv()
    import auth_session

    if args.logout:
        auth_session.logout()
        print("  [Zeno Auth] Logged out and cleared local session.")
        sys.exit(0)

    if args.whoami:
        info = auth_session.get_session_info()
        if info.get("authenticated"):
            who = info.get("email") or info.get("name") or info.get("mode")
            print(f"  [OK] Signed in ({who} via {info.get('source')})")
            sys.exit(0)
        print("  [i] Not signed in. Run: python3 scripts/setup.py --login")
        sys.exit(1)

    if args.login:
        ok_login = auth_session.interactive_login()
        sys.exit(0 if ok_login else 1)

    print("=" * 72)
    print(f"Zeno Setup — {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Workspace Root : {common.ROOT}")
    print("=" * 72)

    ff_ok = check_or_setup_ffmpeg()
    venv_ok = check_or_setup_venv(init_venv=args.init_venv, prewarm=args.prewarm)
    assets_ok = verify_assets()
    check_env()
    try:
        import clean as _clean_mod
        removed_mcp = _clean_mod._cleanup_legacy_claude_mcp()
        if removed_mcp > 0:
            print(f"\n[Clean] Removed {removed_mcp} legacy Zeno MCP server entry(s) from user Claude config.")
        if _clean_mod._ensure_codex_project_trust(common.ROOT):
            print("[Codex] Trusted this Zeno workspace in ~/.codex/config.toml (enables Zeno Cloud network access).")
    except Exception:
        pass
    smoke_ok = smoke_test() if args.smoke else True

    print("\n" + "=" * 72)
    if ff_ok and venv_ok and assets_ok and smoke_ok:
        print("READY: Zeno cross-platform environment and all asset packs verified!")
    else:
        print("WARNING: Some checks reported missing components; review the output above.")
    print("=" * 72)


if __name__ == "__main__":
    main()
