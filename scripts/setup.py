#!/usr/bin/env python3
"""One-command cross-platform bootstrap & health check for Claude Editor (macOS & Windows).

Usage:
    python3 scripts/setup.py              # Verify FFmpeg, venv, Wav2Vec2 cache, and all asset packs
    python3 scripts/setup.py --init-venv  # Create local .venv and install ML/audio dependencies
    python3 scripts/setup.py --prewarm    # Pre-download torchaudio WAV2VEC2_ASR_BASE_960H checkpoint
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


REQUIRED_ASSETS = {
    "Face Detection ONNX (YuNet)": common.YUNET_MODEL,
    "Font (Montserrat-ExtraBold)": common.FONTS_DIR / "Montserrat-ExtraBold.ttf",
    "Overlay (video_call_bar)": common.OVERLAYS_DIR / "video_call_bar.png",
    "Overlay (split_border)": common.OVERLAYS_DIR / "split_border.png",
    "Overlay (nova_phone)": common.OVERLAYS_DIR / "nova_phone.png",
    "Template (mark_packs.json)": common.TEMPLATES_DIR / "mark_packs.json",
    "Template (supernova_layouts.json)": common.TEMPLATES_DIR / "supernova_layouts.json",
    "Template (video_call_pip.json)": common.TEMPLATES_DIR / "video_call_pip.json",
    "Template (stop_saying_banners.json)": common.TEMPLATES_DIR / "stop_saying_banners.json",
    "Template (caption_pill.json)": common.TEMPLATES_DIR / "caption_pill.json",
    "SFX Library Map": common.SFX_DIR / "sfx_library_map.json",
}


def check_or_setup_ffmpeg() -> bool:
    print("\n[1/5] Checking FFmpeg & FFprobe...")
    bundled_dir = common.ROOT / "tools" / "ffmpeg"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    target_bin = bundled_dir / ("ffmpeg.exe" if common.IS_WIN else "ffmpeg")
    target_probe = bundled_dir / ("ffprobe.exe" if common.IS_WIN else "ffprobe")

    # Auto-copy from sibling ScriptCut-AI/bin if local tools/ffmpeg binary is missing
    if not target_bin.is_file():
        sib_bin = common.SCRIPTCUT_ROOT / "bin" / ("ffmpeg.exe" if common.IS_WIN else "ffmpeg")
        if sib_bin.is_file():
            shutil.copy2(sib_bin, target_bin)
            if not common.IS_WIN:
                target_bin.chmod(0o755)
            print(f"  + Copied FFmpeg from sibling ScriptCut-AI -> {target_bin}")

    if not target_probe.is_file():
        sib_probe = common.SCRIPTCUT_ROOT / "bin" / ("ffprobe.exe" if common.IS_WIN else "ffprobe")
        if sib_probe.is_file():
            shutil.copy2(sib_probe, target_probe)
            if not common.IS_WIN:
                target_probe.chmod(0o755)
            print(f"  + Copied FFprobe from sibling ScriptCut-AI -> {target_probe}")

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
    if init_venv and not (common.ROOT / ".venv").exists():
        print("  + Creating local virtual environment at .venv ...")
        subprocess.run([sys.executable, "-m", "venv", str(common.ROOT / ".venv")], check=True)
        venv_py = common.find_venv_python()
        pkgs = [
            "torch",
            "torchaudio",
            "transformers",
            "opencv-python-headless",
            "numpy",
            "soundfile",
            "Pillow",
            "google-genai",
        ]
        print(f"  + Installing packages into {venv_py} ...")
        subprocess.run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip", *pkgs], check=True)

    venv_py = common.find_venv_python()
    print(f"  [OK] Venv Python: {venv_py}")

    check_code = (
        "import importlib, json\n"
        "mods = ['torch', 'torchaudio', 'transformers', 'cv2', 'numpy', 'soundfile', 'PIL']\n"
        "status = {}\n"
        "for m in mods:\n"
        "    try:\n"
        "        mod = importlib.import_module(m)\n"
        "        status[m] = getattr(mod, '__version__', 'installed')\n"
        "    except Exception:\n"
        "        status[m] = None\n"
        "print(json.dumps(status))\n"
    )
    try:
        r = subprocess.run([str(venv_py), "-c", check_code], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            pkg_status = common. json.loads(r.stdout.strip().splitlines()[-1])
            found = [f"{k} ({v})" for k, v in pkg_status.items() if v]
            missing = [k for k, v in pkg_status.items() if not v]
            print(f"  [OK] Installed  : {', '.join(found)}")
            if missing:
                print(f"  [!] Missing optional packages in {venv_py}: {', '.join(missing)}")
    except Exception as exc:
        print(f"  [!] Could not inspect packages in {venv_py}: {exc}")

    print("\n[3/5] Checking Wav2Vec2 CTC Alignment Checkpoint Cache...")
    ckpt_dir = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"
    wav2vec_ckpts = list(ckpt_dir.glob("*wav2vec2*")) if ckpt_dir.is_dir() else []
    if wav2vec_ckpts:
        for ckpt in wav2vec_ckpts:
            mb = ckpt.stat().st_size / (1024 * 1024)
            print(f"  [OK] Cached checkpoint: {ckpt} ({mb:.1f} MB)")
    elif prewarm:
        print("  + Pre-warming torchaudio WAV2VEC2_ASR_BASE_960H checkpoint...")
        prewarm_code = "import torchaudio; torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.get_model()"
        subprocess.run([str(venv_py), "-c", prewarm_code], check=False)
    else:
        print(f"  [i] Wav2Vec2 checkpoint not yet cached in {ckpt_dir} (will auto-download on first CTC alignment or run with --prewarm).")

    return True


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
        ok = r_sfx.is_file() and w_sfx.is_file()
        all_ok = all_ok and ok
        status = "OK" if ok else "MISSING"
        print(f"  [{status}] SFX Pack  '{pack:<8}' -> right={r_sfx.name}, wrong={w_sfx.name}")

    for label, path in REQUIRED_ASSETS.items():
        exists = path.is_file()
        all_ok = all_ok and exists
        status = "OK" if exists else "MISSING"
        print(f"  [{status}] {label:<38}: {path.relative_to(common.ROOT)}")

    return all_ok


def check_env() -> None:
    print("\n[5/5] Checking Environment & API Configuration...")
    local_env = common.ROOT / ".env"
    sibling_env = common.SCRIPTCUT_ROOT / ".env"
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    if local_env.is_file():
        print(f"  [OK] Found local .env ({local_env})")
    elif sibling_env.is_file():
        print(f"  [OK] Using fallback sibling .env ({sibling_env})")
    else:
        print("  [i] No .env file found. Copy .env.example to .env and add your GEMINI_API_KEY.")
    print(f"  [{'OK' if has_gemini else 'i'}] GEMINI_API_KEY configured: {'Yes' if has_gemini else 'No (offline/heuristic fallback active)'}")


def main():
    ap = argparse.ArgumentParser(description="Bootstrap and verify Claude Editor cross-platform setup.")
    ap.add_argument("--init-venv", action="store_true", help="Create local .venv and install dependencies")
    ap.add_argument("--prewarm", action="store_true", help="Pre-download Wav2Vec2 checkpoint into torch hub cache")
    args = ap.parse_args()

    print("=" * 72)
    print(f"Claude Editor Setup — {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Workspace Root : {common.ROOT}")
    print("=" * 72)

    ff_ok = check_or_setup_ffmpeg()
    venv_ok = check_or_setup_venv(init_venv=args.init_venv, prewarm=args.prewarm)
    assets_ok = verify_assets()
    check_env()

    print("\n" + "=" * 72)
    if ff_ok and venv_ok and assets_ok:
        print("READY: Claude Editor cross-platform environment and all asset packs verified!")
    else:
        print("WARNING: Some checks reported missing components; review the output above.")
    print("=" * 72)


if __name__ == "__main__":
    main()
