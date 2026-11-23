"""Shared paths, environment loaders, media probing, GPU encoding, and delivery helpers for Zeno.

Standard library only (runs on any Python 3.10+ across macOS, Windows, and Linux).
Bytecode writing is disabled at import time so running via a symlinked `.venv` never writes `.pyc` files.
"""
from __future__ import annotations

import ast
import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import datetime
import hashlib
import json
import math
import platform
import re
import shutil
import subprocess
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 console output on Windows & non-UTF-8 terminals for Indic scripts
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

IS_WIN = sys.platform.startswith("win") or os.name == "nt"
IS_MAC = sys.platform == "darwin"
ARCH = platform.machine().lower()

ROOT = Path(__file__).resolve().parent.parent

# Project-local keys that must never leak into external fallback loading
_LOCAL_ONLY_ENV_KEYS = frozenset({
    "FFMPEG_PATH",
    "FFPROBE_PATH",
    "ZENO_OUTPUT_DIR",
    "SQLITE_DB_PATH",
})


def load_dotenv(dotenv_path: Optional[Path | str] = None, override: bool = False) -> Dict[str, str]:
    """Load `.env` and `.zeno_session.json` variables using the Python standard library.

    Resolution order:
      1. `ROOT / '.env'` (primary)
      2. `ROOT / '.zeno_session.json'` (OAuth session fallback for missing cloud keys)
    """
    candidates: List[tuple[Path, bool]] = []
    if dotenv_path is not None:
        candidates.append((Path(dotenv_path), True))
    else:
        candidates.append((ROOT / ".env", True))

    loaded: Dict[str, str] = {}
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
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            elif "#" in val:
                val = val.split("#", 1)[0].strip()

            if not is_primary and key in _LOCAL_ONLY_ENV_KEYS:
                continue

            if (override and is_primary) or key not in os.environ:
                os.environ[key] = val
                loaded[key] = val

    # Auto-load OAuth session from ROOT/.zeno_session.json
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

# CPU & Thermal Governor: prevent OpenMP / Accelerate / OpenBLAS / PyTorch / FFmpeg from spawning unbounded threads
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

ASSETS = ROOT / "assets"
SFX_DIR = ASSETS / "sfx"
MARKS_DIR = ASSETS / "marks"
OVERLAYS_DIR = ASSETS / "overlays"
FONTS_DIR = ASSETS / "fonts"
TEMPLATES_DIR = ASSETS / "templates"
TRANSITIONS_DIR = ASSETS / "transitions"
PREMIERE_DIR = ASSETS / "premiere"
SUBTITLES_DIR = ROOT / "subtitles"
FORMATS_DIR = ROOT / "formats"
MODELS_DIR = ROOT / "models"

os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

YUNET_MODEL = next(
    (
        p
        for p in (
            MODELS_DIR / "face_detection_yunet_2023mar.onnx",
            ASSETS / "vision" / "face_detection_yunet_2023mar.onnx",
        )
        if p.is_file()
    ),
    ASSETS / "vision" / "face_detection_yunet_2023mar.onnx",
)

WORK = ROOT / "work"
# Scratch / unnamed local outputs stay in ROOT / "output"; named job deliveries go to ~/Downloads/Zeno Outs/<Name>/
OUTPUT = ROOT / "output"
DOWNLOADS_DIR = Path.home() / "Downloads"
ZENO_OUTS_DIR = DOWNLOADS_DIR / "Zeno Outs"


def _is_runnable_binary(path: Path) -> bool:
    """Return True if path exists, is a file, and matches the host OS binary convention."""
    if not path or not path.is_file():
        return False
    if not IS_WIN and path.suffix.lower() == ".exe":
        return False
    return True


def resolve_ffmpeg() -> Path:
    """Auto-resolve the FFmpeg binary across macOS (arm64/Intel), Windows (x64), and Linux."""
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        for cand in (Path(env_path), ROOT / env_path):
            if _is_runnable_binary(cand):
                return cand.resolve()

    bin_name = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    bundled = ROOT / "tools" / "ffmpeg" / bin_name
    if _is_runnable_binary(bundled):
        return bundled

    which_ff = shutil.which("ffmpeg")
    if which_ff and _is_runnable_binary(Path(which_ff)):
        return Path(which_ff)
    for sys_cand in (Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
        if _is_runnable_binary(sys_cand):
            return sys_cand

    return bundled


def resolve_ffprobe() -> Path:
    """Auto-resolve the FFprobe binary across macOS, Windows, and Linux."""
    env_probe = os.environ.get("FFPROBE_PATH")
    if env_probe:
        for cand in (Path(env_probe), ROOT / env_probe):
            if _is_runnable_binary(cand):
                return cand.resolve()

    bin_name = "ffprobe.exe" if IS_WIN else "ffprobe"
    bundled = ROOT / "tools" / "ffmpeg" / bin_name
    if _is_runnable_binary(bundled):
        return bundled

    which_fp = shutil.which("ffprobe")
    if which_fp and _is_runnable_binary(Path(which_fp)):
        return Path(which_fp)
    for sys_cand in (Path("/opt/homebrew/bin/ffprobe"), Path("/usr/local/bin/ffprobe")):
        if _is_runnable_binary(sys_cand):
            return sys_cand

    return bundled


def resolve_node() -> Optional[Path]:
    """Resolve the Node.js binary for the `subtitles/` caption renderer."""
    env_node = os.environ.get("ZENO_NODE_PATH", "").strip()
    if env_node and _is_runnable_binary(Path(env_node)):
        return Path(env_node)

    which_node = shutil.which("node")
    if which_node and _is_runnable_binary(Path(which_node)):
        return Path(which_node)

    rel_node = Path("node.exe") if IS_WIN else Path("bin") / "node"
    for cand in (
        ROOT / "tools" / "node" / rel_node,
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
    ):
        if _is_runnable_binary(cand):
            return cand

    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        nvm_bins = sorted(nvm_dir.glob("*/bin/node"))
        for cand in reversed(nvm_bins):
            if _is_runnable_binary(cand):
                return cand

    return None


def find_venv_python() -> Path:
    """Auto-resolve the Python interpreter with ML/audio dependencies (torch, cv2, torchaudio, etc.)."""
    env_py = os.environ.get("CLAUDE_EDITOR_PYTHON")
    if env_py:
        for cand in (Path(env_py), ROOT / env_py):
            if _is_runnable_binary(cand):
                return cand

    def _venv_cands(base: Path) -> List[Path]:
        if IS_WIN:
            return [
                base / ".venv" / "Scripts" / "python.exe",
                base / ".venv" / "bin" / "python",
                base / ".venv" / "bin" / "python3",
            ]
        return [
            base / ".venv" / "bin" / "python",
            base / ".venv" / "bin" / "python3",
            base / ".venv" / "Scripts" / "python.exe",
        ]

    for cand in _venv_cands(ROOT):
        if _is_runnable_binary(cand):
            return cand

    return Path(sys.executable)


FFMPEG = resolve_ffmpeg()
FFPROBE = resolve_ffprobe()
VENV_PYTHON = find_venv_python()
NODE = resolve_node()

DEFAULT_SFX = {"right": "correct_ding.wav", "wrong": "sn_wrong.wav"}
# Meta Ads & Social Media Reels/Shorts Loudness Standard (-10.5 LUFS, -1.0 dBTP peak cap)
DEFAULT_LOUDNESS_LUFS = -10.5
DEFAULT_PEAK_CAP_DB = -1.0

MARK_PACKS = {
    "default": {"right": "right.png", "wrong": "wrong.png"},
    "glossy": {"right": "glossy_right.png", "wrong": "glossy_wrong.png"},
    "flash": {"right": "green_flash.png", "wrong": "red_flash.png"},
    "vignette": {"right": "w42_green_vignette.png", "wrong": "w42_red_vignette.png"},
    "supernova": {"right": "glossy_right.png", "wrong": "glossy_wrong.png"},
    "kn20228": {"right": "green_flash.png", "wrong": "red_flash.png"},
    "w42": {"right": "w42_green_vignette.png", "wrong": "w42_red_vignette.png"},
}

SFX_PACKS = {
    "default": {
        "right": "correct_ding.wav",
        "wrong": "sn_wrong.wav",
        "transition": "whoosh.wav",
        "swoosh": "whoosh.wav",
        "swish": "sn_swish_1.wav",
        "pop": "pop.wav",
        "click": "click.wav",
        "riser": "sn_riser_1.wav",
        "ding": "ding.wav",
    },
    "supernova": {
        "right": "sn_correct.wav",
        "wrong": "sn_wrong.wav",
        "transition": "sn_swish_1.wav",
        "swoosh": "sn_swish_1.wav",
        "swish": "sn_swish_1.wav",
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
        "swish": "kn20228_swoosh.wav",
        "click": "kn20228_click.wav",
        "pop": "pop.wav",
        "riser": "sn_riser_1.wav",
    },
    "w42": {
        "right": "w42_right.wav",
        "wrong": "sn_wrong.wav",
        "transition": "w42_transition.wav",
        "swoosh": "w42_swoosh.wav",
        "swish": "w42_swoosh.wav",
        "click": "click.wav",
        "pop": "pop.wav",
        "riser": "sn_riser_1.wav",
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


def resolve_mark_asset(mark_type: str, style: Optional[str] = None) -> Path:
    """Resolve the PNG overlay path for a tick ('right') or error ('wrong') mark."""
    norm_type = _normalize_mark_type(mark_type)
    style_key = (style or os.environ.get("CLAUDE_EDITOR_MARK_STYLE") or "default").strip()

    if style_key.lower() in MARK_PACKS:
        pack = MARK_PACKS[style_key.lower()]
        if norm_type in pack:
            return MARKS_DIR / pack[norm_type]

    packs_json = TEMPLATES_DIR / "mark_packs.json"
    if packs_json.is_file():
        try:
            custom_packs = json.loads(packs_json.read_text(encoding="utf-8")).get("mark_packs", {})
            if style_key in custom_packs and norm_type in custom_packs[style_key]:
                return resolve(custom_packs[style_key][norm_type])
        except Exception:
            pass

    paired = MARKS_DIR / f"{style_key}_{norm_type}.png"
    if paired.is_file():
        return paired

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

    raw_type = str(mark_type or "").strip()
    for cand in (MARKS_DIR / raw_type, MARKS_DIR / f"{raw_type}.png", OVERLAYS_DIR / f"{raw_type}.png"):
        if cand.is_file():
            return cand

    fallback_name = MARK_PACKS["default"].get(norm_type, f"{norm_type}.png")
    return MARKS_DIR / fallback_name


def resolve_sfx_asset(sfx_key_or_file: str, pack: Optional[str] = None) -> Path:
    """Resolve an SFX file from a named SFX pack ('default', 'supernova', 'kn20228', 'w42') or filename."""
    pack_key = (pack or os.environ.get("CLAUDE_EDITOR_SFX_PACK") or "default").strip().lower()
    key = str(sfx_key_or_file or "right").strip()
    key_lower = key.lower()
    base_lower = Path(key.replace("\\", "/")).name.lower()

    if base_lower in ("riser", "riser.wav") and (SFX_DIR / "sn_riser_1.wav").is_file():
        return SFX_DIR / "sn_riser_1.wav"

    if key_lower in ("right", "wrong", "correct", "error", "check", "cross", "tick"):
        norm = _normalize_mark_type(key_lower)
        sfx_map = SFX_PACKS.get(pack_key, SFX_PACKS["default"])
        return SFX_DIR / sfx_map.get(norm, DEFAULT_SFX.get(norm, "correct_ding.wav"))

    if key_lower in SFX_PACKS.get(pack_key, {}):
        return SFX_DIR / SFX_PACKS[pack_key][key_lower]

    for cand in (Path(key), ROOT / key, SFX_DIR / key, SFX_DIR / f"{key}.wav", SFX_DIR / f"{key}.mp3"):
        if cand.is_file():
            return cand

    alias_map = {
        "swish": "sn_swish_1.wav",
        "swish.wav": "sn_swish_1.wav",
        "sn_whoosh_soft": "sn_swish_1.wav",
        "sn_whoosh_soft.wav": "sn_swish_1.wav",
        "w42_flash_whoosh": "w42_transition.wav",
        "w42_flash_whoosh.wav": "w42_transition.wav",
    }
    if base_lower in alias_map and (SFX_DIR / alias_map[base_lower]).is_file():
        return SFX_DIR / alias_map[base_lower]

    return SFX_DIR / key


def resolve_mark_sfx(mark_type: str, sfx: Optional[str] = "default", pack: Optional[str] = None) -> Optional[Path]:
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


def resolve(p: Any) -> Path:
    """Resolve a path relative to ROOT, with cross-platform fallback for Windows paths on macOS."""
    if isinstance(p, Path) and p.exists():
        return p if p.is_absolute() else p.resolve()

    s = str(p).strip()
    s_norm = s.replace("\\", "/")
    basename = s_norm.rsplit("/", 1)[-1] if "/" in s_norm else s_norm

    cand = Path(s).expanduser()
    if cand.is_absolute() and cand.exists():
        return cand

    rel_cand = ROOT / s_norm
    if rel_cand.exists():
        return rel_cand

    is_win_drive = bool(re.match(r"^[A-Za-z]:/", s_norm))
    if is_win_drive or not (cand.exists() or rel_cand.exists()):
        for sub in ("input/footage/", "input/script/", "input/", "work/", "output/", "assets/", "models/", "formats/"):
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
_fingerprints: Dict[tuple, str] = {}

DERIVED_BY_TRANSCRIPT = ("gemini_transcript.json", "transcript.txt")
DERIVED_BY_ALIGN = ("words.json", "hq_words.json", "align.json")
_engine_versions: Dict[str, str] = {}


def _source_digest(paths, extra_chunks: Optional[List[str]] = None) -> str:
    """Hash of source files ignoring blank lines and `#` comments so comment edits do not invalidate caches."""
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
    for chunk in extra_chunks or ():
        for line in str(chunk).splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                h.update(t.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:10]


def _extract_prompt_source(gt_path: Path) -> str:
    """Extract only prompt/lang-normalization definitions from `gemini_transcriber.py`.

    Retry, timeout, and HTTP transport edits do not change transcription semantics and
    must not invalidate existing cached transcripts across workspaces.
    """
    try:
        src = gt_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
        parts: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("build_transcribe_prompt", "normalize_lang_code"):
                seg = ast.get_source_segment(src, node)
                if seg:
                    parts.append(seg)
        return "\n".join(parts)
    except Exception:
        return ""


def engine_versions() -> Dict[str, str]:
    """Return content digests for transcript, alignment, and vision engines for automatic cache invalidation."""
    if not _engine_versions:
        ae = ROOT / "scripts" / "align_engine"
        transcript_paths = [ae / "guardian.py", ae / "lexicon.py", *ae.glob("lexicons/*.py")]
        prompt_slice = _extract_prompt_source(ae / "gemini_transcriber.py")
        align = [
            ae / "acoustic.py",
            ae / "viterbi.py",
            ae / "onset_verifier.py",
            ae / "pipeline.py",
            ae / "__init__.py",
            *ae.glob("phonetics/*.py"),
        ]
        _engine_versions.update({
            "transcript": _source_digest(transcript_paths, extra_chunks=[prompt_slice] if prompt_slice else None),
            "align": _source_digest(align),
            "vision": _source_digest([ROOT / "scripts" / "vision_tracker.py"]),
        })
    return dict(_engine_versions)


def ensure_cache_current(wdir: Path | str) -> List[str]:
    """Drop stale or damaged cached files inside `wdir` when engine code changes or `ZENO_FRESH=1` is set."""
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
    fresh = os.environ.get("ZENO_FRESH") == "1"
    transcript_changed = old is not None and old.get("transcript") != now["transcript"]
    if fresh:
        drop.update(DERIVED_BY_TRANSCRIPT + DERIVED_BY_ALIGN + ("transcript_meta.json",))
    elif transcript_changed:
        drop.update(DERIVED_BY_TRANSCRIPT + DERIVED_BY_ALIGN + ("transcript_meta.json",))
    elif old is not None and old.get("align") != now["align"]:
        drop.update(DERIVED_BY_ALIGN)
    vision_stale = fresh or old is None or old.get("vision") != now.get("vision")
    removed_vision: List[str] = []
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
    removed: List[str] = []
    for name in sorted(drop):
        f = wdir / name
        if f.is_file():
            f.unlink()
            removed.append(name)
    if fresh or transcript_changed:
        chunks_dir = wdir / "chunks"
        if chunks_dir.is_dir():
            shutil.rmtree(chunks_dir, ignore_errors=True)
            removed.append("chunks")
    removed += removed_vision
    if old != now or removed:
        write_json(marker, now)
    return removed


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", s)


def clip_work_dir(src: Any) -> Path:
    """Cache folder for one source clip: `work/_clips/<stem>-<fingerprint>`."""
    stem = Path(str(src)).stem
    p = resolve(src)
    if p.is_file():
        try:
            st = p.stat()
            key = (str(p), st.st_size, st.st_mtime_ns)
            if key not in _fingerprints:
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


def run(
    cmd,
    quiet: bool = True,
    low_priority: bool = False,
    timeout: Optional[float] = None,
    cwd: Optional[Path | str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run an external command; raise RuntimeError with the tail of stderr if it exits non-zero."""
    exec_cmd = [str(c) for c in cmd]
    kwargs: Dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    proc_env = dict(os.environ if env is None else env)
    proc_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    kwargs["env"] = proc_env
    if low_priority:
        if IS_WIN and hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS"):
            kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        elif IS_MAC and Path("/usr/sbin/taskpolicy").is_file():
            exec_cmd = ["/usr/sbin/taskpolicy", "-c", "utility", *exec_cmd]
        elif not IS_WIN and hasattr(os, "nice"):
            kwargs["preexec_fn"] = lambda: os.nice(10)
    r = subprocess.run(exec_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed ({r.returncode}): {cmd[0]}\n{r.stderr[-3000:]}")
    if not quiet:
        print(r.stderr)
    return r


_HW_VIDEO_ENCODERS = ("h264_nvenc", "h264_videotoolbox", "h264_qsv", "h264_amf")
_VIDEO_INPUT_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ts")


def _uses_hw_or_copy_encoder(str_args: List[str]) -> bool:
    if any(enc in str_args for enc in _HW_VIDEO_ENCODERS):
        return True
    for idx in range(len(str_args) - 1):
        if str_args[idx] in ("-c:v", "-vcodec") and str_args[idx + 1] == "copy":
            return True
    return False


def ffmpeg(*args, timeout: float = 180):
    str_args = [str(a) for a in args]
    hw_or_copy = _uses_hw_or_copy_encoder(str_args)
    t_cap = str(min(int(ZENO_MAX_THREADS), 2)) if (IS_MAC or not hw_or_copy) else str(int(ZENO_MAX_THREADS))
    base = [
        FFMPEG,
        "-hide_banner",
        "-y",
        "-filter_threads",
        t_cap,
        "-filter_complex_threads",
        t_cap,
    ]
    if "-threads" not in str_args and len(args) >= 2:
        if hw_or_copy:
            return run([*base, "-threads", t_cap, *args], low_priority=True, timeout=timeout)
        return run([*base, "-threads", t_cap, *args[:-1], "-threads", t_cap, args[-1]], low_priority=True, timeout=timeout)
    return run([*base, *args], low_priority=True, timeout=timeout)


def run_cool() -> None:
    """Run the editing job at background/utility QoS priority so laptops stay cool and quiet (`ZENO_COOL=0` disables)."""
    if os.environ.get("ZENO_COOL", "1").strip().lower() in ("0", "off", "false", "no") or os.environ.get("_ZENO_COOL"):
        return
    if not sys.argv or not Path(sys.argv[0]).is_file():
        return
    os.environ["_ZENO_COOL"] = "1"
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        if IS_MAC and Path("/usr/sbin/taskpolicy").is_file():
            os.execv("/usr/sbin/taskpolicy", ["taskpolicy", "-c", "utility", sys.executable, "-B", *sys.argv])
        elif IS_WIN:
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        elif hasattr(os, "nice"):
            os.nice(10)
    except Exception:
        pass


def thermal_pause(max_wait: float = 90.0, step: float = 10.0) -> None:
    """On macOS under active thermal throttling (`pmset -g therm`), pause briefly to let the CPU cool."""
    if not IS_MAC:
        return
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
            print(f"[cool] CPU thermally throttled (speed limit {m.group(1)}%); pausing briefly to cool down...", flush=True)
        time.sleep(step)
        waited += step


# ----------------------------------------------------------------------------- Delivery & Project Naming
def strip_ratio_prefix(name: str) -> str:
    """Strip a leading 'reel' or 'SQ' ratio prefix from a Notion or video name so prefixes never double up."""
    s = str(name or "").strip()
    stripped = re.sub(r"^(?:reel|sq)[\s_-]+", "", s, flags=re.IGNORECASE).strip()
    return stripped or s


def slugify(text: str, fallback: str = "project") -> str:
    """Convert any project or script title into a clean, portable filesystem slug."""
    norm = unicodedata.normalize("NFKD", str(text or "")).strip()
    slug = re.sub(r"[^\w\s-]+", "", norm, flags=re.ASCII)
    slug = re.sub(r"[-\s_]+", "_", slug).strip("_").lower()[:80]
    if slug:
        return slug
    # Fallback hash suffix when input contains only non-ASCII characters
    raw = str(text or "").strip()
    if raw:
        return f"{fallback}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"
    return fallback


def job_folder_name(name: str) -> str:
    """Safe human-readable delivery folder name from a Notion or video title."""
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", strip_ratio_prefix(name)).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)[:180].strip()
    return cleaned or "Zeno Edit"


def _delivery_base_dir() -> Path:
    """Base directory for named job deliveries: `ZENO_OUTPUT_DIR` if explicitly set, else `~/Downloads/Zeno Outs`."""
    override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return DOWNLOADS_DIR / "Zeno Outs"


def output_dir_for_name(name: str, unique: bool = False) -> Path:
    """Return `~/Downloads/Zeno Outs/<clean_name>` (or `$ZENO_OUTPUT_DIR/<clean_name>`), or `OUTPUT` when `name` is empty."""
    raw = str(name or "").strip()
    if not raw:
        return OUTPUT
    clean_name = job_folder_name(raw)
    base = _delivery_base_dir()
    folder = base / clean_name
    if unique:
        n = 2
        while folder.is_dir() and any(f.suffix.lower() in (".mp4", ".prproj") for f in folder.iterdir()):
            folder = base / f"{clean_name} ({n})"
            n += 1
    return folder


def resolve_output_dir(
    name: str = "",
    out_dir: Optional[Path | str] = None,
    unique: bool = False,
) -> Path:
    """Resolve the target output directory for a job (`out_dir` override -> named `~/Downloads/Zeno Outs/<Name>/` -> scratch `OUTPUT`)."""
    if out_dir is not None and str(out_dir).strip():
        return Path(str(out_dir).strip()).expanduser().resolve()
    if str(name or "").strip():
        return output_dir_for_name(name, unique=unique)
    override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return OUTPUT


def unique_job_output(name: str) -> Path:
    """`~/Downloads/Zeno Outs/<name>/reel_<name>.mp4` (or `<name> (2)` if `<name>` already holds a finished render)."""
    folder = output_dir_for_name(name, unique=True)
    return folder / f"reel_{folder.name}.mp4"


def square_job_output(src_mp4: Path | str) -> Path:
    """`SQ_<name>.mp4` next to the 9:16 reel video (`reel_<name>.mp4` -> `SQ_<name>.mp4`)."""
    p = Path(src_mp4)
    return p.with_name(f"SQ_{strip_ratio_prefix(p.stem)}.mp4")


# ----------------------------------------------------------------------------- Session & Multi-User Isolation
def active_user_email() -> str:
    """Return the currently signed-in user email (lowercased), or empty string if none."""
    env_em = os.environ.get("ZENO_USER_EMAIL", "").strip().lower()
    if env_em:
        return env_em
    sess_file = ROOT / ".zeno_session.json"
    try:
        if sess_file.is_file():
            d = json.loads(sess_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(d, dict):
                em = str(d.get("email") or "").strip().lower()
                if em:
                    return em
    except Exception:
        pass
    return ""


def read_job_owner(work_dir: Path | str) -> str:
    """Read the owner email of a `work/<project>` directory from `owner.json` or `progress.json`."""
    d = Path(work_dir)
    for fn, key in (("owner.json", "owner_email"), ("progress.json", "owner_email")):
        p = d / fn
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    em = str(data.get(key) or "").strip().lower()
                    if em:
                        return em
            except Exception:
                continue
    return ""


def stamp_job_owner(work_dir: Path | str, email: Optional[str] = None) -> str:
    """Persist `owner.json` inside `work/<project>` once so projects stay isolated per login."""
    d = Path(work_dir)
    existing = read_job_owner(d)
    if existing:
        owner_file = d / "owner.json"
        if not owner_file.is_file():
            try:
                d.mkdir(parents=True, exist_ok=True)
                owner_file.write_text(json.dumps({"owner_email": existing}, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass
        return existing
    em = (email if email is not None else active_user_email()).strip().lower()
    if not em:
        return ""
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner_email": em,
            "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (d / "owner.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return em


def resolve_user_project_name(base_name: str, work_root: Optional[Path] = None, email: Optional[str] = None) -> str:
    """Ensure two different logins on the same computer never collide on `work/<project>`."""
    wroot = Path(work_root) if work_root is not None else WORK
    em = (email if email is not None else active_user_email()).strip().lower()
    cand = wroot / base_name
    if not em or not cand.is_dir():
        return base_name
    existing_owner = read_job_owner(cand)
    if not existing_owner or existing_owner == em:
        return base_name
    slug = re.sub(r"[^a-z0-9]+", "_", em.split("@", 1)[0]).strip("_")[:24] or "user"
    return f"{base_name}__{slug}"


# ----------------------------------------------------------------------------- Job Window Markers
JOB_MARKER = WORK / "_job_marker.json"


def _job_marker_path() -> Path:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") if os.environ.get("CLAUDECODE") else os.environ.get("CODEX_THREAD_ID")
    session = session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session:
        suffix = hashlib.sha256(session.encode()).hexdigest()[:16]
        return JOB_MARKER.with_name(f"{JOB_MARKER.stem}.{suffix}{JOB_MARKER.suffix}")
    return JOB_MARKER


def _read_job_marker() -> dict:
    try:
        marker = json.loads(_job_marker_path().read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            return {}
        if marker.get("start"):
            marker["start"] = float(marker["start"])
        return marker
    except (OSError, ValueError):
        return {}


def _write_job_marker(marker: dict) -> None:
    path = _job_marker_path()
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(marker), encoding="utf-8")
        temp.replace(path)
    except OSError:
        pass
    finally:
        temp.unlink(missing_ok=True)


def job_start_epoch(create: bool = True) -> float:
    """Start timestamp of the current session's active editing job."""
    marker = _read_job_marker()
    now = time.time()
    if marker.get("start") and 0 <= now - float(marker["start"]) < 6 * 3600:
        return float(marker["start"])
    if create:
        _write_job_marker({"start": now})
    return now


def job_begin(reuse_minutes: float = 30.0, identity: str = "") -> float:
    """Begin or reuse an active job window matching `identity`."""
    marker = _read_job_marker()
    now = time.time()
    same_edit = not identity or not marker.get("identity") or marker["identity"] == identity
    if same_edit and marker.get("start") and 0 <= now - float(marker["start"]) < reuse_minutes * 60:
        start = float(marker["start"])
    else:
        start = now
    _write_job_marker({"start": start, "identity": identity or marker.get("identity", "")})
    return start


def job_done() -> None:
    """Clear the current session's job window marker."""
    try:
        _job_marker_path().unlink()
    except OSError:
        pass


# ----------------------------------------------------------------------------- GPU Compute & Hardware Encoding
def compute_device() -> str:
    """Select aligner device: NVIDIA CUDA -> Apple Silicon MPS -> CPU (`ZENO_DEVICE` overrides)."""
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


def _has_nvidia_smi() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    if IS_WIN:
        sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        return any(
            p.is_file()
            for p in (
                sys_root / "System32" / "nvidia-smi.exe",
                prog_files / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
            )
        )
    return Path("/usr/bin/nvidia-smi").is_file()


def gpu_report() -> Dict[str, str]:
    """Report active ML compute device, hardware name, active FFmpeg encoder, and optional setup hint."""
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
                    name = (
                        subprocess.run(
                            ["sysctl", "-n", "machdep.cpu.brand_string"],
                            capture_output=True,
                            text=True,
                            timeout=3,
                        ).stdout.strip()
                        or name
                    )
                except Exception:
                    pass
        elif IS_WIN and _has_nvidia_smi() and "+cu" not in torch.__version__:
            hint = "NVIDIA GPU detected with CPU PyTorch build; install CUDA PyTorch to enable GPU alignment"
        elif os.environ.get("ZENO_DEVICE", "auto").lower() == "cpu":
            hint = "ZENO_DEVICE=cpu forces CPU execution"
    except Exception:
        pass
    return {"device": device, "name": name, "encoder": hardware_encoder() or "libx264", "hint": hint}


_HW_ENCODER: Dict[str, Any] = {}
_HW_CANDIDATES = {
    "darwin": ["h264_videotoolbox"],
    "win": ["h264_nvenc", "h264_qsv", "h264_amf"],
    "linux": ["h264_nvenc", "h264_qsv"],
}


def _probe_hw_encoder(cand: str, nvenc_presets: tuple[str, str] = ("p5", "p3")) -> bool:
    v_args = hw_video_args(cand, crf=18, draft=False, bitrate_kbps=12000, nvenc_presets=nvenc_presets)
    cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=1080x1920:d=0.1:r=30",
        "-frames:v",
        "2",
        *v_args,
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "null",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def hardware_encoder() -> str:
    """Return the first verified H.264 hardware encoder on this host ('' if software libx264 only)."""
    if "name" in _HW_ENCODER:
        return _HW_ENCODER["name"]
    forced = os.environ.get("ZENO_ENCODER", "auto").strip().lower()
    name = ""
    if forced not in ("cpu", "libx264", "x264", "off", "0"):
        key = "darwin" if IS_MAC else ("win" if IS_WIN else "linux")
        try:
            listed = subprocess.run([str(FFMPEG), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout or ""
        except Exception:
            listed = ""
        for cand in ([forced] if forced.startswith("h264_") else _HW_CANDIDATES.get(key, [])):
            if cand not in listed:
                continue
            try:
                if cand == "h264_nvenc":
                    for presets in (("p5", "p3"), ("fast", "fast")):
                        if _probe_hw_encoder(cand, nvenc_presets=presets):
                            _HW_ENCODER["nvenc_presets"] = presets
                            name = cand
                            break
                    if name:
                        break
                elif _probe_hw_encoder(cand):
                    name = cand
                    break
            except Exception:
                continue
    _HW_ENCODER["name"] = name
    return name


def final_bitrate_kbps(duration_sec: float, scale: float = 1.0, target_mb: Optional[float] = None) -> int:
    """Compute target video bitrate (8,000-12,000 kbps at 1080x1920) aiming near `target_mb` (default 150 MB)."""
    mb = float(target_mb if target_mb is not None else os.environ.get("ZENO_TARGET_MB", "150"))
    dur = max(1.0, float(duration_sec or 1.0))
    kbps = (mb * 8000.0 / dur) - 200.0
    kbps = max(8000.0, min(12000.0, kbps))
    return int(kbps * scale)


def hw_video_args(
    name: str,
    crf: int = 18,
    draft: bool = False,
    bitrate_kbps: Optional[int] = None,
    nvenc_presets: Optional[tuple[str, str]] = None,
    allow_bframes: bool = True,
    gop: Optional[int] = None,
) -> list:
    """FFmpeg encoder flags matching `libx264 -crf <crf>` quality on the given hardware encoder."""
    q = 28 if draft else (14 if crf <= 14 else 19)
    if name == "h264_videotoolbox":
        vt = ["-c:v", name, "-b:v", "8M" if draft else (f"{bitrate_kbps}k" if bitrate_kbps else "16M"), "-allow_sw", "1"]
        if not allow_bframes:
            vt.extend(["-bf", "0"])
        if gop is not None:
            vt.extend(["-g", str(int(gop))])
        return vt
    if name == "h264_nvenc":
        p_main, p_draft = nvenc_presets or _HW_ENCODER.get("nvenc_presets", ("p5", "p3"))
        if bitrate_kbps and not draft and bitrate_kbps > 20000:
            q_eff = min(q, 15)
            nv_cap: list = []
        elif bitrate_kbps and not draft:
            q_eff = q
            maxrate_k = min(int(bitrate_kbps * 1.25), 20000)
            bufsize_k = min(int(bitrate_kbps * 2), 28000)
            nv_cap = ["-maxrate", f"{maxrate_k}k", "-bufsize", f"{bufsize_k}k"]
        else:
            q_eff = q
            nv_cap = []
        nv = ["-c:v", name, "-preset", p_draft if draft else p_main, "-rc", "vbr", "-cq", str(q_eff), "-b:v", "0"] + nv_cap
        if not allow_bframes:
            nv.extend(["-bf", "0"])
        if gop is not None:
            nv.extend(["-g", str(int(gop))])
        return nv
    if name == "h264_qsv":
        qsv = ["-c:v", name, "-global_quality", str(q + 2), "-preset", "veryfast" if draft else "medium"]
        if not allow_bframes:
            qsv.extend(["-bf", "0"])
        if gop is not None:
            qsv.extend(["-g", str(int(gop))])
        return qsv
    if name == "h264_amf":
        return ["-c:v", name, "-quality", "speed" if draft else "quality", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
    return ["-c:v", name]


def _inject_hwaccel_inputs(a: List[str]) -> List[str]:
    input_positions: List[int] = []
    prev_i_end = 0
    for idx in range(len(a) - 1):
        if a[idx] == "-i":
            target = a[idx + 1].lower()
            between = a[prev_i_end:idx]
            prev_i_end = idx + 2
            if any(tok in ("-f", "-loop", "-hwaccel") for tok in between):
                continue
            if target.endswith(_VIDEO_INPUT_EXTS):
                input_positions.append(idx)
    if not (1 <= len(input_positions) <= 4):
        return a
    pos_set = set(input_positions)
    out: List[str] = []
    for idx, tok in enumerate(a):
        if idx in pos_set:
            out.extend(["-hwaccel", "auto"])
        out.append(tok)
    return out


def with_hardware_encoder(
    args,
    draft: bool = False,
    bitrate_kbps: Optional[int] = None,
    hwaccel_decode: bool = False,
    allow_bframes: Optional[bool] = None,
) -> Optional[List[str]]:
    """Rewrite an FFmpeg `libx264` argument list to use the verified hardware encoder, or return None."""
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
    out_arg = a[-1] if a else ""
    is_mkv = str(out_arg).lower().endswith(".mkv")
    eff_allow_bframes = (not is_mkv) if allow_bframes is None else bool(allow_bframes)
    has_bf = "-bf" in a
    has_g = "-g" in a
    head = _inject_hwaccel_inputs(a[:i]) if hwaccel_decode else a[:i]
    v_args = hw_video_args(
        name,
        crf,
        draft,
        bitrate_kbps,
        allow_bframes=(True if has_bf else eff_allow_bframes),
        gop=(None if (has_g or eff_allow_bframes) else 30),
    )
    out, k = head + v_args, i + 2
    while k < len(a):
        if a[k] in ("-preset", "-crf", "-tune") and k + 1 < len(a):
            k += 2
            continue
        out.append(a[k])
        k += 1
    return out


def ffmpeg_encode(
    *args,
    timeout: float = 180,
    draft: bool = False,
    hardware: bool = True,
    bitrate_kbps: Optional[int] = None,
    hw_decode: bool = True,
    allow_bframes: Optional[bool] = None,
):
    """Run an FFmpeg H.264 encode on the GPU when available, falling back cleanly to software `libx264`."""
    if hardware:
        hw_dec = with_hardware_encoder(
            args, draft, bitrate_kbps, hwaccel_decode=hw_decode, allow_bframes=allow_bframes
        )
        hw_enc = with_hardware_encoder(
            args, draft, bitrate_kbps, hwaccel_decode=False, allow_bframes=allow_bframes
        )
        if hw_dec is not None:
            try:
                return ffmpeg(*hw_dec, timeout=timeout)
            except Exception as exc:
                if hw_enc is not None and hw_enc != hw_dec:
                    try:
                        return ffmpeg(*hw_enc, timeout=timeout)
                    except Exception as exc2:
                        exc = exc2
                enc_name = hardware_encoder() or "gpu"
                last_line = (str(exc).strip().splitlines() or ["encode error"])[-1][:180]
                print(f"  [GPU Encoder] {enc_name} failed ({last_line}); falling back to CPU libx264.", flush=True)
    cpu_args = list(args)
    if len(cpu_args) > 1:
        str_args = [str(x) for x in cpu_args]
        is_mkv = str_args[-1].lower().endswith(".mkv")
        eff_allow_bframes = (not is_mkv) if allow_bframes is None else bool(allow_bframes)
        extra: List[Any] = []
        if not eff_allow_bframes and "libx264" in str_args:
            if "-bf" not in str_args:
                extra.extend(["-bf", "0"])
            if "-g" not in str_args:
                extra.extend(["-g", "30"])
        if bitrate_kbps and not draft:
            cpu_args = [
                ("16" if i > 0 and cpu_args[i - 1] == "-crf" and str(x) == "18" else x)
                for i, x in enumerate(cpu_args[:-1])
            ] + [
                "-maxrate",
                f"{int(bitrate_kbps * 1.25)}k",
                "-bufsize",
                f"{int(bitrate_kbps * 2)}k",
                *extra,
                cpu_args[-1],
            ]
        elif extra:
            cpu_args = cpu_args[:-1] + extra + [cpu_args[-1]]
    return ffmpeg(*cpu_args, timeout=timeout)


# ----------------------------------------------------------------------------- Media Probing & File I/O
def ffprobe_json(path: Any, timeout: float = 30.0) -> Dict[str, Any]:
    """Run `ffprobe -of json -show_format -show_streams` and return the parsed dictionary (`{}` on error)."""
    resolved = resolve(path)
    try:
        r = subprocess.run(
            [
                str(FFPROBE),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(resolved),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if r.returncode != 0 or not r.stdout:
            return {}
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def probe(path: Any) -> Dict[str, Any]:
    """Return `{path, duration, width, height, fps, rotation, has_audio}` (display dimensions with rotation applied)."""
    resolved = resolve(path)
    r = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(resolved)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    err = r.stderr
    info: Dict[str, Any] = {
        "path": str(resolved),
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "rotation": 0,
        "has_audio": "Audio:" in err,
    }
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

    if info["duration"] is None:
        fp = ffprobe_json(resolved)
        fmt_dur = (fp.get("format") or {}).get("duration")
        if fmt_dur is not None:
            try:
                info["duration"] = float(fmt_dur)
            except (TypeError, ValueError):
                pass
    return info


def probe_media(path: Any) -> Dict[str, Any]:
    """Alias and structured media probe wrapper returning `{path, duration, width, height, fps, rotation, has_audio}`."""
    return probe(path)


_MISSING = object()


def write_json(path: Path | str, data: Any) -> None:
    """Atomically write formatted UTF-8 JSON so interrupted writes never leave partial files."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path | str, default: Any = _MISSING) -> Any:
    """Read UTF-8 JSON from `path` (returns `default` when provided and the file is missing or unreadable)."""
    p = Path(path)
    if default is _MISSING:
        return json.loads(p.read_text(encoding="utf-8"))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _docx_to_text(path: Path) -> str:
    """Extract plain text from a `.docx` file (paragraphs and table rows in reading order) via standard library."""
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
                    cells = [
                        " ".join(para_text(p).strip() for p in tc_el.iter(w + "p")).strip()
                        for tc_el in tr.findall(w + "tc")
                    ]
                    cells = [c for c in cells if c]
                    if len(cells) >= 3 and re.fullmatch(r"L\d+", cells[0]):
                        lines.append(f"{cells[0]} {cells[1]}: {' '.join(cells[2:])}")
                    elif len(cells) == 2:
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


def read_script_text(path: Path | str) -> str:
    """Read a script (`.txt`/`.md`/`.docx`) as NFC-normalized UTF-8 text."""
    p = Path(path)
    raw = _docx_to_text(p) if p.suffix.lower() == ".docx" else p.read_text(encoding="utf-8-sig", errors="replace")
    return unicodedata.normalize("NFC", raw).replace("\u00a0", " ")


def _parse_bool_flag(val: Any) -> bool:
    """Parse boolean flag across bool, numeric, and string ('1'/'true'/'yes'/'on' vs '0'/'false'/'off') inputs."""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return math.isfinite(val) and val != 0
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("", "0", "false", "no", "off", "none", "null"):
            return False
        return True
    return bool(val)


def segment_speed(seg: Optional[Dict[str, Any]]) -> float:
    """Return playback speed factor from `seg['speed']`, clamped to `[0.25, 4.0]` (defaults to `1.0`)."""
    if not isinstance(seg, dict):
        return 1.0
    raw = seg.get("speed")
    if raw is None or isinstance(raw, bool):
        return 1.0
    if isinstance(raw, str):
        raw = raw.strip().rstrip("xX").strip()
        if not raw:
            return 1.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(val) or val <= 0.0:
        return 1.0
    return max(0.25, min(4.0, val))


def segment_is_muted(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True if `seg` mutes source audio (`mute` or `muted` truthy, or `audio_mode` muted)."""
    if not isinstance(seg, dict):
        return False
    if _parse_bool_flag(seg.get("mute")) or _parse_bool_flag(seg.get("muted")):
        return True
    audio_mode = str(seg.get("audio_mode") or "").strip().lower()
    return audio_mode in ("mute", "muted", "off", "none")


def segment_is_beat(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True if `seg` is a non-spoken visual beat (`beat` or `silent` truthy)."""
    if not isinstance(seg, dict):
        return False
    return _parse_bool_flag(seg.get("beat")) or _parse_bool_flag(seg.get("silent"))


def segment_hold_tail(seg: Optional[Dict[str, Any]]) -> float:
    """Return non-negative post-cut hold tail duration (seconds) from `seg` (`hold_tail` or `hold`)."""
    if not isinstance(seg, dict):
        return 0.0
    try:
        val = float(seg.get("hold_tail") or seg.get("hold") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, val) if math.isfinite(val) else 0.0


def segment_play_duration(seg: Dict[str, Any]) -> float:
    """Return moving playback duration (seconds): `max(0.0, end - start) / segment_speed(seg)`."""
    if not isinstance(seg, dict):
        return 0.0
    try:
        st = float(seg.get("start", 0.0) or 0.0)
        en = float(seg.get("end", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, en - st) / segment_speed(seg)


def segment_duration(seg: Dict[str, Any]) -> float:
    """Return effective segment duration (`segment_play_duration(seg) + segment_hold_tail(seg)`)."""
    return segment_play_duration(seg) + segment_hold_tail(seg)


def src_to_seg_time(seg: Dict[str, Any], src_t: float) -> float:
    """Map an absolute source-clip timestamp `src_t` to segment-relative playback seconds."""
    st = float(seg.get("start", 0.0) or 0.0) if isinstance(seg, dict) else 0.0
    return (float(src_t) - st) / segment_speed(seg)


def seg_to_src_time(seg: Dict[str, Any], seg_t: float) -> float:
    """Map segment-relative playback seconds `seg_t` back to an absolute source-clip timestamp."""
    st = float(seg.get("start", 0.0) or 0.0) if isinstance(seg, dict) else 0.0
    return st + min(max(0.0, float(seg_t)), segment_play_duration(seg)) * segment_speed(seg)


def tc(sec: float) -> str:
    """Format seconds as `mm:ss.ff` for human-readable logs."""
    m, s = divmod(max(float(sec or 0.0), 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"
