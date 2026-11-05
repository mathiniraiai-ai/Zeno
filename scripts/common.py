"""Shared paths and helpers. Standard library only (runs on any Python 3.10+ across macOS & Windows)."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Windows consoles default to cp1252, which can't print Kannada/Tamil notes
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

IS_WIN = sys.platform.startswith("win") or os.name == "nt"
IS_MAC = sys.platform == "darwin"
ARCH = platform.machine().lower()

ROOT = Path(__file__).resolve().parent.parent
SCRIPTCUT_ROOT = ROOT.parent / "ScriptCut-AI"


def load_dotenv(dotenv_path=None, override=False):
    """Load .env variables using the Python standard library (no python-dotenv needed).

    Loads from ROOT / '.env' first, then falls back to sibling ScriptCut-AI / '.env'
    for any variables not already set.
    """
    candidates = []
    if dotenv_path is not None:
        candidates.append((Path(dotenv_path), True))
    else:
        candidates.append((ROOT / ".env", True))
        candidates.append((SCRIPTCUT_ROOT / ".env", False))

    loaded = {}
    for path, is_primary in candidates:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if not key:
                continue
            # Strip matching quotes or inline comments on unquoted values
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            elif "#" in val:
                val = val.split("#", 1)[0].strip()

            # Do not inherit a relative FFMPEG_PATH (like ./bin/ffmpeg) from fallback ScriptCut-AI/.env
            if not is_primary and key == "FFMPEG_PATH" and not Path(val).is_absolute():
                continue

            if override or key not in os.environ:
                os.environ[key] = val
                loaded[key] = val

    # Auto-load local zero-copy OAuth session from .zeno_session.json if present
    session_path = ROOT / ".zeno_session.json"
    if session_path.is_file():
        try:
            sess = json.loads(session_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(sess, dict):
                sess_map = {
                    "ZENO_CLOUD_TOKEN": str(sess.get("token") or "").strip(),
                    "SCRIPTCUT_CLOUD_TOKEN": str(sess.get("token") or "").strip(),
                    "ZENO_CLOUD_URL": str(sess.get("cloud_url") or "").strip(),
                    "SCRIPTCUT_CLOUD_URL": str(sess.get("cloud_url") or "").strip(),
                    "ZENO_USER_EMAIL": str(sess.get("email") or "").strip(),
                    "ZENO_USER_NAME": str(sess.get("name") or "").strip(),
                }
                for k, v in sess_map.items():
                    if v and (override or not os.environ.get(k)):
                        os.environ[k] = v
                        loaded[k] = v
        except Exception:
            pass

    # Mirror ZENO_CLOUD_URL <-> SCRIPTCUT_CLOUD_URL and ZENO_CLOUD_TOKEN <-> SCRIPTCUT_CLOUD_TOKEN
    for a, b in (
        ("ZENO_CLOUD_URL", "SCRIPTCUT_CLOUD_URL"),
        ("ZENO_CLOUD_TOKEN", "SCRIPTCUT_CLOUD_TOKEN"),
    ):
        va = os.environ.get(a, "").strip()
        vb = os.environ.get(b, "").strip()
        if va and not vb:
            os.environ[b] = va
            loaded[b] = va
        elif vb and not va:
            os.environ[a] = vb
            loaded[a] = vb

    return loaded


# Load environment variables before resolving tool paths
load_dotenv()

# CPU & Thermal Governor: prevent OpenMP / Accelerate / OpenBLAS / PyTorch / FFmpeg
# from spawning unbounded threads across all cores and overheating laptops/PCs.
ZENO_MAX_THREADS = max(1, int(os.environ.get("ZENO_MAX_THREADS", "4") or "4"))
for _thr_var in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "TORCH_NUM_THREADS",
):
    os.environ.setdefault(_thr_var, str(ZENO_MAX_THREADS))


def _is_runnable_binary(path: Path) -> bool:
    """Return True if path exists, is a file, and matches the host OS binary convention."""
    if not path or not path.is_file():
        return False
    if not IS_WIN and path.suffix.lower() == ".exe":
        return False
    return True


def resolve_ffmpeg() -> Path:
    """Auto-resolve the FFmpeg binary across macOS (arm64/Intel) and Windows (x64)."""
    # 1. Check os.environ.get("FFMPEG_PATH")
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        for cand in (Path(env_path), ROOT / env_path, SCRIPTCUT_ROOT / env_path):
            if _is_runnable_binary(cand):
                return cand.resolve()

    # 2. Check ROOT / "tools" / "ffmpeg" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
    bundled = ROOT / "tools" / "ffmpeg" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
    if _is_runnable_binary(bundled):
        return bundled

    # 3. Check shutil.which("ffmpeg") and Homebrew /usr/local locations
    which_ff = shutil.which("ffmpeg")
    if which_ff and _is_runnable_binary(Path(which_ff)):
        return Path(which_ff)
    for sys_cand in (Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
        if _is_runnable_binary(sys_cand):
            return sys_cand

    # 4. Check sibling ScriptCut-AI/bin/ffmpeg
    sibling_cands = [
        SCRIPTCUT_ROOT / "bin" / ("ffmpeg.exe" if IS_WIN else "ffmpeg"),
    ]
    for sib in sibling_cands:
        if _is_runnable_binary(sib):
            return sib

    # Fallback to default bundled location
    return bundled


def resolve_ffprobe() -> Path:
    """Auto-resolve the FFprobe binary or wrapper script across macOS and Windows."""
    # 1. Check os.environ.get("FFPROBE_PATH") or sibling to FFMPEG_PATH
    env_probe = os.environ.get("FFPROBE_PATH")
    if env_probe:
        for cand in (Path(env_probe), ROOT / env_probe, SCRIPTCUT_ROOT / env_probe):
            if _is_runnable_binary(cand):
                return cand.resolve()

    # 2. Check ROOT / "tools" / "ffmpeg" / ("ffprobe.exe" if IS_WIN else "ffprobe")
    bundled = ROOT / "tools" / "ffmpeg" / ("ffprobe.exe" if IS_WIN else "ffprobe")
    if _is_runnable_binary(bundled):
        return bundled

    # 3. Check shutil.which("ffprobe") and Homebrew /usr/local locations
    which_fp = shutil.which("ffprobe")
    if which_fp and _is_runnable_binary(Path(which_fp)):
        return Path(which_fp)
    for sys_cand in (Path("/opt/homebrew/bin/ffprobe"), Path("/usr/local/bin/ffprobe")):
        if _is_runnable_binary(sys_cand):
            return sys_cand

    # 4. Check sibling ScriptCut-AI/bin/ffprobe
    sibling_cands = [
        SCRIPTCUT_ROOT / "bin" / ("ffprobe.exe" if IS_WIN else "ffprobe"),
    ]
    for sib in sibling_cands:
        if _is_runnable_binary(sib):
            return sib

    return bundled


def find_venv_python() -> Path:
    """Auto-resolve the Python interpreter with ML/audio dependencies (torch, cv2, librosa, etc.)."""
    # 1. Check os.environ.get("CLAUDE_EDITOR_PYTHON")
    env_py = os.environ.get("CLAUDE_EDITOR_PYTHON")
    if env_py:
        for cand in (Path(env_py), ROOT / env_py):
            if _is_runnable_binary(cand):
                return cand.resolve()

    # 2. Check ROOT / ".venv"
    local_venv_cands = (
        [ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python", ROOT / ".venv" / "bin" / "python3"]
        if IS_WIN
        else [ROOT / ".venv" / "bin" / "python", ROOT / ".venv" / "bin" / "python3", ROOT / ".venv" / "Scripts" / "python.exe"]
    )
    for cand in local_venv_cands:
        if _is_runnable_binary(cand):
            return cand

    # 3. Check sibling ScriptCut-AI/.venv (macOS & Windows)
    sibling_venv_cands = (
        [
            SCRIPTCUT_ROOT / ".venv" / "Scripts" / "python.exe",
            SCRIPTCUT_ROOT / ".venv" / "bin" / "python",
            SCRIPTCUT_ROOT / ".venv" / "bin" / "python3",
        ]
        if IS_WIN
        else [
            SCRIPTCUT_ROOT / ".venv" / "bin" / "python",
            SCRIPTCUT_ROOT / ".venv" / "bin" / "python3",
            SCRIPTCUT_ROOT / ".venv" / "Scripts" / "python.exe",
        ]
    )
    for cand in sibling_venv_cands:
        if _is_runnable_binary(cand):
            return cand

    return Path(sys.executable)


FFMPEG = resolve_ffmpeg()
FFPROBE = resolve_ffprobe()
VENV_PYTHON = find_venv_python()

SFX_DIR = ROOT / "assets" / "sfx"
MARKS_DIR = ROOT / "assets" / "marks"
OVERLAYS_DIR = ROOT / "assets" / "overlays"
FONTS_DIR = ROOT / "assets" / "fonts"
TEMPLATES_DIR = ROOT / "assets" / "templates"
MODELS_DIR = ROOT / "models"
YUNET_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
WORK = ROOT / "work"
# Finished videos go to Downloads/Zeno Outputs on every PC (team rule); ZENO_OUTPUT_DIR in .env overrides it.
OUTPUT = Path(os.environ.get("ZENO_OUTPUT_DIR", "").strip() or (Path.home() / "Downloads" / "Zeno Outputs")).expanduser()

DEFAULT_SFX = {"right": "correct_ding.wav", "wrong": "sn_wrong.wav"}
# Meta Ads & Social Media Reels/Shorts Loudness Standard (-11.5 LUFS, -1.0 dBTP peak cap)
DEFAULT_LOUDNESS_LUFS = -11.5
DEFAULT_PEAK_CAP_DB = -1.0

# Named Swappable Mark Packs (tick / error badges & full-screen flashes/vignettes)
MARK_PACKS = {
    "default": {
        "right": "right.png",
        "wrong": "wrong.png",
    },
    "glossy": {
        "right": "glossy_right.png",
        "wrong": "glossy_wrong.png",
    },
    "flash": {
        "right": "green_flash.png",
        "wrong": "red_flash.png",
    },
    "vignette": {
        "right": "w42_green_vignette.png",
        "wrong": "w42_red_vignette.png",
    },
    # Convenient project aliases
    "supernova": {
        "right": "glossy_right.png",
        "wrong": "glossy_wrong.png",
    },
    "kn20228": {
        "right": "green_flash.png",
        "wrong": "red_flash.png",
    },
    "w42": {
        "right": "w42_green_vignette.png",
        "wrong": "w42_red_vignette.png",
    },
}

# Named Swappable SFX Packs
SFX_PACKS = {
    "default": {
        "right": "correct_ding.wav",
        "wrong": "sn_wrong.wav",
        "transition": "whoosh.wav",
        "swoosh": "whoosh.wav",
        "pop": "pop.wav",
        "click": "click.wav",
        "riser": "riser.wav",
        "ding": "ding.wav",
    },
    "supernova": {
        "right": "sn_correct.wav",
        "wrong": "sn_wrong.wav",
        "transition": "sn_swish_1.wav",
        "swoosh": "sn_swish_1.wav",
        "pop": "sn_punch_1.wav",
        "punch": "sn_punch_2.wav",
        "click": "sn_click.mp3",
        "riser": "sn_riser_1.wav",
    },
    "kn20228": {
        "right": "kn20228_correct.mp3",
        "wrong": "sn_wrong.wav",
        "transition": "kn20228_swoosh.wav",
        "swoosh": "kn20228_swoosh.wav",
        "click": "kn20228_click.wav",
        "pop": "pop.wav",
    },
    "w42": {
        "right": "w42_right.wav",
        "wrong": "sn_wrong.wav",
        "transition": "w42_transition.wav",
        "swoosh": "w42_swoosh.wav",
        "click": "click.wav",
        "pop": "pop.wav",
    },
}


def _normalize_mark_type(mark_type: str) -> str:
    """Normalize mark type aliases ('right'/'tick'/'check'/'correct' vs 'wrong'/'error'/'cross'/'x')."""
    t = str(mark_type or "right").strip().lower()
    if t in ("right", "correct", "check", "tick", "green", "ok", "true", "yes"):
        return "right"
    if t in ("wrong", "error", "cross", "x", "red", "false", "no", "incorrect"):
        return "wrong"
    return t


def resolve_mark_asset(mark_type: str, style: str = None) -> Path:
    """Resolve the PNG overlay path for a tick ('right') or error ('wrong') mark.

    Supports:
    - Built-in mark packs: 'default', 'glossy', 'flash', 'vignette' (plus 'supernova', 'kn20228', 'w42')
    - User-defined packs in assets/templates/mark_packs.json
    - Auto-paired custom files in assets/marks/<style>_right.png & <style>_wrong.png
    - Any direct filename in assets/marks/<custom>.png or path relative to ROOT
    """
    norm_type = _normalize_mark_type(mark_type)
    style_key = (style or os.environ.get("CLAUDE_EDITOR_MARK_STYLE") or "default").strip()

    # 1. If style_key is in built-in MARK_PACKS
    if style_key.lower() in MARK_PACKS:
        pack = MARK_PACKS[style_key.lower()]
        if norm_type in pack:
            return MARKS_DIR / pack[norm_type]

    # 2. Check assets/templates/mark_packs.json for custom user-added packs
    packs_json = TEMPLATES_DIR / "mark_packs.json"
    if packs_json.is_file():
        try:
            custom_packs = json.loads(packs_json.read_text(encoding="utf-8")).get("mark_packs", {})
            if style_key in custom_packs and norm_type in custom_packs[style_key]:
                return resolve(custom_packs[style_key][norm_type])
        except Exception:
            pass

    # 3. Check if style_key is a prefix for <style>_right.png / <style>_wrong.png in assets/marks/
    paired = MARKS_DIR / f"{style_key}_{norm_type}.png"
    if paired.is_file():
        return paired

    # 4. Check if style_key is an explicit filename or path (e.g. "glossy_right.png" or "custom_badge")
    for cand in (
        Path(style_key),
        ROOT / style_key,
        MARKS_DIR / style_key,
        MARKS_DIR / f"{style_key}.png",
        OVERLAYS_DIR / style_key,
        OVERLAYS_DIR / f"{style_key}.png",
    ):
        if cand.is_file():
            return cand

    # 5. Check if mark_type itself is a custom filename in assets/marks/ (e.g. mark_type="glossy_wrong")
    raw_type = str(mark_type or "").strip()
    for cand in (MARKS_DIR / raw_type, MARKS_DIR / f"{raw_type}.png", OVERLAYS_DIR / f"{raw_type}.png"):
        if cand.is_file():
            return cand

    # 6. Fallback to default right.png / wrong.png
    fallback_name = MARK_PACKS["default"].get(norm_type, f"{norm_type}.png")
    return MARKS_DIR / fallback_name


def resolve_sfx_asset(sfx_key_or_file: str, pack: str = None) -> Path:
    """Resolve an SFX file from a named SFX pack ('default', 'supernova', 'kn20228', 'w42') or filename."""
    pack_key = (pack or os.environ.get("CLAUDE_EDITOR_SFX_PACK") or "default").strip().lower()
    key = str(sfx_key_or_file or "right").strip()
    key_lower = key.lower()

    # If the caller passed a pack name as the sfx value, resolve from that pack
    if key_lower in ("right", "wrong", "correct", "error", "check", "cross", "tick"):
        norm = _normalize_mark_type(key_lower)
        sfx_map = SFX_PACKS.get(pack_key, SFX_PACKS["default"])
        return SFX_DIR / sfx_map.get(norm, DEFAULT_SFX.get(norm, "correct_ding.wav"))

    if key_lower in SFX_PACKS.get(pack_key, {}):
        return SFX_DIR / SFX_PACKS[pack_key][key_lower]

    for cand in (Path(key), ROOT / key, SFX_DIR / key, SFX_DIR / f"{key}.wav", SFX_DIR / f"{key}.mp3"):
        if cand.is_file():
            return cand

    return SFX_DIR / key


def resolve_mark_sfx(mark_type: str, sfx: str = "default", pack: str = None) -> Path:
    """Resolve the SFX file for a mark ('right'/'wrong') given an optional sfx override or sfx pack."""
    if sfx is None:
        return None
    norm_type = _normalize_mark_type(mark_type)
    sfx_str = str(sfx).strip()
    if not sfx_str or sfx_str.lower() == "default":
        return resolve_sfx_asset(norm_type, pack=pack)
    if sfx_str.lower() in SFX_PACKS:
        return resolve_sfx_asset(norm_type, pack=sfx_str.lower())
    return resolve_sfx_asset(sfx_str, pack=pack)


def resolve(p) -> Path:
    """Resolve a path relative to ROOT, with cross-platform fallback for Windows paths on macOS."""
    if isinstance(p, Path) and p.exists():
        return p if p.is_absolute() else p.resolve()

    s = str(p).strip()
    s_norm = s.replace("\\", "/")
    basename = s_norm.rsplit("/", 1)[-1] if "/" in s_norm else s_norm

    cand = Path(s)
    if cand.is_absolute() and cand.exists():
        return cand

    rel_cand = ROOT / s_norm
    if rel_cand.exists():
        return rel_cand

    # Handle Windows drive paths (e.g. "D:/AI Projects/.../S1.mp4") or moved footage paths
    is_win_drive = bool(re.match(r"^[A-Za-z]:/", s_norm))
    if is_win_drive or not (cand.exists() or rel_cand.exists()):
        for sub in ("input/footage/", "input/script/", "input/", "work/", "output/", "assets/", "models/"):
            idx = s_norm.lower().find(sub)
            if idx != -1:
                mapped = ROOT / s_norm[idx:]
                if mapped.exists():
                    return mapped
        if basename:
            footage_cand = ROOT / "input" / "footage" / basename
            if footage_cand.exists():
                return footage_cand
            script_cand = ROOT / "input" / "script" / basename
            if script_cand.exists():
                return script_cand
        if is_win_drive and not IS_WIN and basename:
            return ROOT / "input" / "footage" / basename

    return cand if cand.is_absolute() else ROOT / s_norm


CLIP_CACHE = WORK / "_clips"
_fingerprints: dict = {}

# Results kept per clip, by what produced them. A change to the code that makes them invalidates them without asking anyone.
DERIVED_BY_TRANSCRIPT = ("gemini_transcript.json", "transcript.txt")
DERIVED_BY_ALIGN = ("words.json", "hq_words.json", "align.json")
_engine_versions: dict = {}


def _source_digest(paths) -> str:
    """Hash of source files with blank lines and # comments left out, so a comment edit does not invalidate caches."""
    import hashlib

    h = hashlib.sha1()
    for p in sorted(str(x) for x in paths):
        try:
            text = Path(p).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                h.update(t.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:10]


def engine_versions() -> dict:
    """{'transcript': ..., 'align': ...}: the transcript version covers the prompt, healing and lexicons; the align
    version covers the aligner. An aligner change therefore never triggers new Gemini calls."""
    if not _engine_versions:
        ae = ROOT / "scripts" / "align_engine"
        transcript = [ae / "gemini_transcriber.py", ae / "guardian.py", ae / "lexicon.py", *ae.glob("lexicons/*.py")]
        align = [ae / "acoustic.py", ae / "viterbi.py", ae / "onset_verifier.py", ae / "pipeline.py", ae / "__init__.py", *ae.glob("phonetics/*.py")]
        _engine_versions.update({"transcript": _source_digest(transcript), "align": _source_digest(align),
                                 "vision": _source_digest([ROOT / "scripts" / "vision_tracker.py"])})
    return dict(_engine_versions)


def ensure_cache_current(wdir) -> list:
    """Drop cached results that older code produced (or that are damaged) so they are rebuilt. Silent: nobody is asked.

    A folder with no version marker is adopted as current (nothing is deleted on the first update); from then on a change
    to the transcript code drops transcript and alignment results, a change to the aligner drops only alignment results.
    `ZENO_FRESH=1` (the --fresh flag) drops everything derived. Returns the file names removed."""
    wdir = Path(wdir)
    if not wdir.is_dir():
        return []
    now = engine_versions()
    marker = wdir / "engine_version.json"
    try:
        old = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    drop = set()
    if os.environ.get("ZENO_FRESH") == "1":
        drop.update(DERIVED_BY_TRANSCRIPT + DERIVED_BY_ALIGN)
    elif old is not None:
        if old.get("transcript") != now["transcript"]:
            drop.update(DERIVED_BY_TRANSCRIPT + DERIVED_BY_ALIGN)
        elif old.get("align") != now["align"]:
            drop.update(DERIVED_BY_ALIGN)
    # Face and shot analysis (vision_*.json) depends on vision_tracker.py: a stale "two_shot" from older code once kept a clip out of a split
    vision_stale = os.environ.get("ZENO_FRESH") == "1" or old is None or old.get("vision") != now.get("vision")
    removed_vision = []
    if vision_stale:
        for f in wdir.glob("vision_*.json"):
            f.unlink()
            removed_vision.append(f.name)
    for name in DERIVED_BY_TRANSCRIPT + DERIVED_BY_ALIGN:
        f = wdir / name
        if name.endswith(".json") and f.is_file():
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                drop.add(name)
    removed = []
    for name in sorted(drop):
        f = wdir / name
        if f.is_file():
            f.unlink()
            removed.append(name)
    removed += removed_vision
    if old != now or removed:
        write_json(marker, now)
    return removed


def clip_work_dir(src) -> Path:
    """Cache folder for one source clip: work/_clips/<stem>-<fingerprint>.

    The fingerprint comes from the file's size plus its first and last 1 MB, so two different clips that share a
    name (S1.mp4 from two videos) never share a transcript or alignment, while the same file in another folder
    keeps its cache. If the file is missing (re-rendering after the footage was moved), the one cache folder for
    that name is used, else the old work/<stem> location.
    """
    stem = Path(str(src)).stem
    p = resolve(src)
    if p.is_file():
        try:
            st = p.stat()
            key = (str(p), st.st_size, st.st_mtime_ns)
            if key not in _fingerprints:
                import hashlib
                h = hashlib.sha1(str(st.st_size).encode())
                with open(p, "rb") as fh:
                    h.update(fh.read(1 << 20))
                    if st.st_size > (2 << 20):
                        fh.seek(-(1 << 20), 2)
                        h.update(fh.read(1 << 20))
                _fingerprints[key] = h.hexdigest()[:10]
            return CLIP_CACHE / f"{stem}-{_fingerprints[key]}"
        except OSError:
            pass
    matches = sorted(CLIP_CACHE.glob(f"{glob_escape(stem)}-??????????")) if CLIP_CACHE.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    return WORK / stem


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", s)


def run(cmd, quiet=True, low_priority=False, timeout=None):
    """Run a command; raise with the tail of stderr if it fails."""
    exec_cmd = [str(c) for c in cmd]
    kwargs = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if low_priority:
        if IS_WIN and hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS"):
            kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        elif IS_MAC and Path("/usr/sbin/taskpolicy").is_file():
            # Pin background media jobs to Apple Silicon Utility/Efficiency QoS tier so P-cores stay cool
            exec_cmd = ["/usr/sbin/taskpolicy", "-c", "utility", *exec_cmd]
        elif not IS_WIN and hasattr(os, "nice"):
            kwargs["preexec_fn"] = lambda: os.nice(10)
    r = subprocess.run(exec_cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kwargs)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed ({r.returncode}): {cmd[0]}\n{r.stderr[-3000:]}")
    if not quiet:
        print(r.stderr)
    return r


def ffmpeg(*args, timeout=180):
    str_args = [str(a) for a in args]
    t_cap = str(min(int(ZENO_MAX_THREADS), 2))
    base = [
        FFMPEG,
        "-hide_banner",
        "-y",
        "-filter_threads", t_cap,
        "-filter_complex_threads", t_cap,
    ]
    if "-threads" not in str_args and len(args) >= 2:
        # Place -threads before input AND before the output file so libx264/encoders are also capped
        return run([*base, "-threads", t_cap, *args[:-1], "-threads", t_cap, args[-1]], low_priority=True, timeout=timeout)
    return run([*base, *args], low_priority=True, timeout=timeout)


# ---------------------------------------------------------------------------------------------------- keep the computer cool
def run_cool() -> None:
    """Run the whole job at low priority so the computer stays cool and quiet. `ZENO_COOL=0` turns it off.

    Mac: the process (and every FFmpeg and PyTorch worker it starts) moves to the Utility/Efficiency QoS tier.
    Windows: BELOW_NORMAL priority class. Linux: nice 10. The GPU does the heavy work; the CPU never runs flat out.
    """
    if os.environ.get("ZENO_COOL", "1").strip().lower() in ("0", "off", "false", "no") or os.environ.get("_ZENO_COOL"):
        return
    if not sys.argv or not Path(sys.argv[0]).is_file():
        return  # imported by a test or another tool: nothing to relaunch
    os.environ["_ZENO_COOL"] = "1"
    try:
        if IS_MAC and Path("/usr/sbin/taskpolicy").is_file():
            os.execv("/usr/sbin/taskpolicy", ["taskpolicy", "-c", "utility", sys.executable, *sys.argv])
        elif IS_WIN:
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        elif hasattr(os, "nice"):
            os.nice(10)
    except Exception:
        pass


def thermal_pause(max_wait: float = 90.0, step: float = 10.0) -> None:
    """On a Mac that reports it is being slowed by heat (`pmset -g therm`), wait for it to cool before the next piece of work."""
    if not IS_MAC:
        return
    import time

    waited = 0.0
    while waited < max_wait:
        try:
            out = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return
        m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
        if not m or int(m.group(1)) >= 90:
            return
        if waited == 0.0:
            print(f"[cool] the CPU is slowed by heat (speed limit {m.group(1)}%); waiting for it to cool down...", flush=True)
        time.sleep(step)
        waited += step


# ---------------------------------------------------------------------------------------------------- GPU use
def strip_ratio_prefix(name: str) -> str:
    """Strip a leading 'reel' or 'SQ' ratio prefix from a Notion or video name so prefixes never double up."""
    s = str(name or "").strip()
    stripped = re.sub(r"^(?:reel|sq)[\s_-]+", "", s, flags=re.IGNORECASE).strip()
    return stripped or s


def job_folder_name(name: str) -> str:
    """A safe folder name from the Notion or video name: no path characters, no leading dots, not empty."""
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", strip_ratio_prefix(name)).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)[:180].strip()
    return cleaned or "Zeno edit"


def unique_job_output(name: str) -> Path:
    """`Zeno Outputs/<name>/reel_<name>.mp4`. Every edit is its own job with its own Notion-named folder: when a folder of that name already
    holds a finished video, this edit gets `<name> (2)`, `<name> (3)`, and so on. Nothing is created here; the render makes the folder."""
    base = job_folder_name(name)
    folder, n = OUTPUT / base, 2
    while folder.is_dir() and any(f.suffix.lower() in (".mp4", ".prproj") for f in folder.iterdir()):
        folder = OUTPUT / f"{base} ({n})"
        n += 1
    return folder / f"reel_{folder.name}.mp4"


def square_job_output(src_mp4: Path) -> Path:
    """`SQ_<name>.mp4` next to the 9:16 reel video (`reel_<name>.mp4` -> `SQ_<name>.mp4`)."""
    p = Path(src_mp4)
    return p.with_name(f"SQ_{strip_ratio_prefix(p.stem)}.mp4")


JOB_MARKER = WORK / "_job_marker.json"


def job_start_epoch(create: bool = True) -> float:
    """When this edit began. Written by the pre-job checks and kept until the render finishes, so the token report counts exactly one
    edit: from the checks to the finished video. (Looking at a whole session's logs mixed several edits and any code work together.)"""
    import time as _t

    try:
        m = json.loads(JOB_MARKER.read_text(encoding="utf-8"))
        if m.get("start") and _t.time() - float(m["start"]) < 6 * 3600:
            return float(m["start"])
    except (OSError, ValueError):
        pass
    now = _t.time()
    if create:
        try:
            JOB_MARKER.parent.mkdir(parents=True, exist_ok=True)
            JOB_MARKER.write_text(json.dumps({"start": now}), encoding="utf-8")
        except OSError:
            pass
    return now


def job_done() -> None:
    """The edit is finished: the next one starts a new window."""
    try:
        JOB_MARKER.unlink()
    except OSError:
        pass


def compute_device() -> str:
    """Where the aligner runs: NVIDIA CUDA, else Apple Silicon MPS, else CPU. `ZENO_DEVICE` (cpu | cuda | mps | auto) forces a choice."""
    want = os.environ.get("ZENO_DEVICE", "auto").strip().lower()
    try:
        import torch
    except Exception:
        return "cpu"
    cuda = torch.cuda.is_available()
    mps = bool(getattr(torch.backends, "mps", None)) and torch.backends.mps.is_available()
    if want == "cpu":
        return "cpu"
    if want == "cuda":
        return "cuda" if cuda else "cpu"
    if want == "mps":
        return "mps" if mps else "cpu"
    return "cuda" if cuda else ("mps" if mps else "cpu")


def gpu_report() -> dict:
    """What the GPU situation is, for the pre-job check: the device in use, its name, and a hint when a GPU exists but is not used."""
    device = compute_device()
    name, hint = "", ""
    try:
        import torch

        if device == "cuda":
            name = torch.cuda.get_device_name(0)
        elif device == "mps":
            name = "Apple GPU"
            if IS_MAC:
                try:
                    name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=3).stdout.strip() or name
                except Exception:
                    pass
        elif IS_WIN and shutil.which("nvidia-smi") and "+cu" not in torch.__version__:
            hint = "an NVIDIA GPU was found but PyTorch is the CPU build: run install.bat again (it installs the GPU build)"
        elif os.environ.get("ZENO_DEVICE", "auto").lower() == "cpu":
            hint = "ZENO_DEVICE=cpu forces the CPU"
    except Exception:
        pass
    return {"device": device, "name": name, "encoder": hardware_encoder() or "libx264", "hint": hint}


_HW_ENCODER: dict = {}
_HW_CANDIDATES = {"darwin": ["h264_videotoolbox"], "win": ["h264_nvenc", "h264_qsv", "h264_amf"], "linux": ["h264_nvenc", "h264_qsv"]}


def hardware_encoder() -> str:
    """The first H.264 hardware encoder that really works on this machine ('' = none): VideoToolbox on a Mac, NVENC / Intel QSV / AMD AMF
    on Windows. A listed encoder can still be unusable without the GPU, so each one is tried with a real two-frame encode.
    `ZENO_ENCODER=cpu` turns it off; `ZENO_ENCODER=h264_nvenc` (or another name) forces one."""
    if "name" in _HW_ENCODER:
        return _HW_ENCODER["name"]
    forced = os.environ.get("ZENO_ENCODER", "auto").strip().lower()
    name = ""
    if forced not in ("cpu", "libx264", "x264", "off", "0"):
        key = "darwin" if IS_MAC else ("win" if IS_WIN else "linux")
        for cand in ([forced] if forced.startswith("h264_") else _HW_CANDIDATES.get(key, [])):
            try:
                listed = subprocess.run([str(FFMPEG), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
                if cand not in listed:
                    continue
                test = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.3:r=10",
                                       "-frames:v", "2", "-c:v", cand, "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True, text=True, timeout=30)
                if test.returncode == 0:
                    name = cand
                    break
            except Exception:
                continue
    _HW_ENCODER["name"] = name
    return name


def hw_video_args(name: str, crf: int = 18, draft: bool = False) -> list:
    """The ffmpeg arguments for one hardware encoder at roughly the quality of x264 `-crf <crf>` (12 = near-lossless intermediate, 18 = final)."""
    q = 28 if draft else (14 if crf <= 14 else 19)
    if name == "h264_videotoolbox":
        return ["-c:v", name, "-b:v", "8M" if draft else "16M", "-allow_sw", "1"]
    if name == "h264_nvenc":
        return ["-c:v", name, "-preset", "p3" if draft else "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
    if name == "h264_qsv":
        return ["-c:v", name, "-global_quality", str(q + 2), "-preset", "veryfast" if draft else "medium"]
    if name == "h264_amf":
        return ["-c:v", name, "-quality", "speed" if draft else "quality", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
    return ["-c:v", name]


def with_hardware_encoder(args, draft: bool = False):
    """A copy of an ffmpeg argument list with libx264 (and its -preset / -crf / -tune) replaced by the hardware encoder; None when there is
    no hardware encoder or the arguments do not use libx264."""
    name = hardware_encoder()
    a = [str(x) for x in args]
    if not name or "libx264" not in a:
        return None
    try:
        i = next(k for k in range(len(a) - 1) if a[k] == "-c:v" and a[k + 1] == "libx264")
    except StopIteration:
        return None
    crf = 18
    if "-crf" in a:
        try:
            crf = int(float(a[a.index("-crf") + 1]))
        except (ValueError, IndexError):
            pass
    out, k = a[:i] + hw_video_args(name, crf, draft), i + 2
    while k < len(a):
        if a[k] in ("-preset", "-crf", "-tune") and k + 1 < len(a):
            k += 2
            continue
        out.append(a[k])
        k += 1
    return out


def ffmpeg_encode(*args, timeout=180, draft: bool = False, hardware: bool = True):
    """Run an ffmpeg command that encodes H.264 on the GPU encoder when the machine has one, and on the CPU (libx264) otherwise or if the
    GPU encode fails."""
    if hardware:
        hw = with_hardware_encoder(args, draft)
        if hw is not None:
            try:
                return ffmpeg(*hw, timeout=timeout)
            except Exception:
                pass
    return ffmpeg(*args, timeout=timeout)


def probe(path):
    """Duration, display size (rotation applied), fps and audio presence, parsed from `ffmpeg -i`."""
    resolved = resolve(path)
    r = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(resolved)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    err = r.stderr
    info = {"path": str(resolved), "duration": None, "width": None, "height": None,
            "fps": None, "rotation": 0, "has_audio": "Audio:" in err}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    vline = next((l for l in err.splitlines() if "Video:" in l), "")
    m = re.search(r", (\d{2,5})x(\d{2,5})", vline)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+) fps", vline)
    if m:
        info["fps"] = float(m.group(1))
    m = re.search(r"rotation of (-?[\d.]+) degrees", err)
    if m:
        info["rotation"] = int(round(float(m.group(1))))
        if abs(info["rotation"]) % 180 == 90 and info["width"]:
            info["width"], info["height"] = info["height"], info["width"]
    return info


def write_json(path, data):
    """Write JSON so a crash or a second process never leaves a half-written file behind."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _docx_to_text(path: Path) -> str:
    """Plain text from a .docx (paragraphs and table rows, in order) using only the standard library."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def para_text(p) -> str:
        out = []
        for el in p.iter():
            if el.tag == w + "t":
                out.append(el.text or "")
            elif el.tag == w + "tab":
                out.append(" ")
            elif el.tag in (w + "br", w + "cr"):
                out.append("\n")
        return "".join(out)

    def walk(parent, lines):
        for child in parent:
            if child.tag == w + "p":
                lines.append(para_text(child))
            elif child.tag == w + "tbl":
                for tr in child.iter(w + "tr"):
                    cells = [" ".join(para_text(p).strip() for p in tc.iter(w + "p")).strip() for tc in tr.findall(w + "tc")]
                    cells = [c for c in cells if c]
                    if len(cells) >= 3 and re.fullmatch(r"L\d+", cells[0]):   # id | speaker | line
                        lines.append(f"{cells[0]} {cells[1]}: {' '.join(cells[2:])}")
                    elif len(cells) == 2:                                      # speaker | line
                        lines.append(f"{cells[0]}: {cells[1]}")
                    else:
                        lines.append(" ".join(cells))
            elif child.tag == w + "sdt":
                content = child.find(w + "sdtContent")
                if content is not None:
                    walk(content, lines)

    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(w + "body")
    lines: list = []
    if body is not None:
        walk(body, lines)
    return "\n".join(lines)


def read_script_text(path) -> str:
    """Read a script (.txt/.md/.docx) as NFC-normalised text so Indic spellings compare equal across editors."""
    p = Path(path)
    raw = _docx_to_text(p) if p.suffix.lower() == ".docx" else p.read_text(encoding="utf-8-sig", errors="replace")
    return unicodedata.normalize("NFC", raw).replace("\u00a0", " ")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tc(sec):
    """Seconds -> mm:ss.ff for human-readable logs."""
    m, s = divmod(max(sec, 0), 60)
    return f"{int(m):02d}:{s:05.2f}"
