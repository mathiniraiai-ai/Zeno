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
    return loaded


# Load environment variables before resolving tool paths
load_dotenv()


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
OUTPUT = ROOT / "output"

DEFAULT_SFX = {"right": "correct_ding.wav", "wrong": "buzzer.wav"}

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
        "wrong": "buzzer.wav",
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
        "wrong": "kn20228_wrong_core.mp3",
        "transition": "kn20228_swoosh.wav",
        "swoosh": "kn20228_swoosh.wav",
        "click": "kn20228_click.wav",
        "pop": "pop.wav",
    },
    "w42": {
        "right": "w42_right.wav",
        "wrong": "w42_wrong.wav",
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


def run(cmd, quiet=True):
    """Run a command; raise with the tail of stderr if it fails."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"Command failed ({r.returncode}): {cmd[0]}\n{r.stderr[-3000:]}")
    if not quiet:
        print(r.stderr)
    return r


def ffmpeg(*args):
    return run([FFMPEG, "-hide_banner", "-y", *args])


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
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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
