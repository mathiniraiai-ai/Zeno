"""Burn-in subtitle engine for rendered edits (`#subs <style> [y=65]` in `plan.edl`).

Spoken words are mapped from each clip's aligned word timestamps onto the finished timeline,
with script-line + speech-span synthesis as a zero-blank fallback whenever an edit cut lacks
word-level alignment. Words are never translated: Indic scripts stay in their native script and
English words stay in English. Caption cards are rendered by `subtitles/render_cues.js` and
`subtitles/caption-renderer.js` (curated styles in `subtitles/styles.json`) on `node-canvas`,
cropped to a horizontal band with 3-subframe active-word karaoke pop, and composited by
`render.py` as a single FFmpeg concat image sequence.
"""
from __future__ import annotations

import colorsys
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SUB_DIR = HERE.parent / "subtitles"

MAX_WORDS_PER_CUE = 4          # Maximum words per cue for Latin scripts
MAX_INDIC_WORDS_PER_CUE = 3    # Maximum words per cue for Indic scripts (wider conjuncts and matras)
MAX_VISUAL_UNITS = 19.0        # Maximum visual width (in character units) that fits a single row at fixed font size
MAX_GAP_IN_CUE = 0.22          # Speech pause > 0.22s starts a fresh caption card
BRIDGE_GAP_MAX = 0.18          # Bridge micro-gaps <= 0.18s within a sentence without overlapping cues
SENTENCE_END = re.compile(r"[.?!।][\"'”’)\]]*$")
CLAUSE_END = re.compile(r"[,;:—–][\"'”’)\]]*$")
INDIC_RE = re.compile(r"[\u0900-\u0D7F]")

DEFAULT_FONT_SIZE = 54         # Fixed single-row caption size in pixels on a 1080px-wide canvas
SINGLE_Y = 65.0                # Default vertical placement (% of height) on full-frame 9:16 cuts
SEAM_Y = 50.0                  # Default vertical placement (% of height) on the split_v center seam
FACE_DROP_Y = 79.0             # Safe lower-zone drop (% of height) when a foreground face occupies y=63..70%
FACE_LOWER_MID_Y0 = 0.63       # Lower-mid face zone top threshold (normalized canvas height)
FACE_LOWER_MID_Y1 = 0.70       # Lower-mid face zone bottom threshold (normalized canvas height)


def caption_font_size(width: int = 1080, spec: Optional[Dict[str, Any]] = None) -> int:
    """Return caption font size in 1080w design-space pixels from `#subs size=`, `ZENO_SUBS_FONT_SIZE`, or `DEFAULT_FONT_SIZE`.

    `subtitles/caption-renderer.js` multiplies `style.fontSize` by `resScale = (width / 1080) * aspectScale`,
    so returning design-space pixels here prevents `(width / 1080)^2` double-shrinking on 720p/540p renders.
    """
    _ = width
    raw = (spec or {}).get("size") or os.environ.get("ZENO_SUBS_FONT_SIZE") or DEFAULT_FONT_SIZE
    try:
        base = min(90.0, max(36.0, float(raw)))
    except (TypeError, ValueError):
        base = float(DEFAULT_FONT_SIZE)
    return int(round(base))


STYLE_ALIASES: Dict[str, int] = {
    "hormozi punch": 4,
    "hormozi": 4,
    "blue box": 4,
    "word box": 4,
    "karaoke": 4,
    "docu": 28,
    "docu bar": 28,
    "docu split bar": 28,
    "split bar": 28,
    "glass": 3,
    "glossy": 3,
    "sticker": 12,
    "cutout": 12,
    "white box": 5,
    "white pill": 32,
    "white karaoke": 32,
    "white karaoke pill": 32,
    "karaoke pill": 32,
    "w47 pill": 32,
    "w47": 32,
    "black box": 30,
    "yellow box": 31,
    "marker": 10,
    "highlighter": 10,
    "rise": 2,
    "cinema": 13,
    "yellow": 13,
}

CONTRACTIONS: Dict[str, List[str]] = {
    "i'm": ["i", "am"],
    "im": ["i", "am"],
    "don't": ["do", "not"],
    "dont": ["do", "not"],
    "can't": ["can", "not"],
    "cant": ["can", "not"],
    "cannot": ["can", "not"],
    "won't": ["will", "not"],
    "wont": ["will", "not"],
    "it's": ["it", "is"],
    "i've": ["i", "have"],
    "ive": ["i", "have"],
    "you're": ["you", "are"],
    "youre": ["you", "are"],
    "we're": ["we", "are"],
    "they're": ["they", "are"],
    "theyre": ["they", "are"],
    "that's": ["that", "is"],
    "thats": ["that", "is"],
    "what's": ["what", "is"],
    "whats": ["what", "is"],
    "let's": ["let", "us"],
    "lets": ["let", "us"],
    "didn't": ["did", "not"],
    "didnt": ["did", "not"],
    "doesn't": ["does", "not"],
    "doesnt": ["does", "not"],
    "isn't": ["is", "not"],
    "isnt": ["is", "not"],
    "aren't": ["are", "not"],
    "arent": ["are", "not"],
    "wasn't": ["was", "not"],
    "wasnt": ["was", "not"],
    "weren't": ["were", "not"],
    "werent": ["were", "not"],
    "couldn't": ["could", "not"],
    "couldnt": ["could", "not"],
    "wouldn't": ["would", "not"],
    "wouldnt": ["would", "not"],
    "shouldn't": ["should", "not"],
    "shouldnt": ["should", "not"],
    "haven't": ["have", "not"],
    "havent": ["have", "not"],
    "hasn't": ["has", "not"],
    "hasnt": ["has", "not"],
    "hadn't": ["had", "not"],
    "hadnt": ["had", "not"],
    "i'll": ["i", "will"],
    "you'll": ["you", "will"],
    "youll": ["you", "will"],
    "we'll": ["we", "will"],
    "they'll": ["they", "will"],
    "theyll": ["they", "will"],
    "he's": ["he", "is"],
    "she's": ["she", "is"],
    "there's": ["there", "is"],
    "theres": ["there", "is"],
    "here's": ["here", "is"],
    "heres": ["here", "is"],
    "who's": ["who", "is"],
    "whos": ["who", "is"],
    "we've": ["we", "have"],
    "weve": ["we", "have"],
    "you've": ["you", "have"],
    "youve": ["you", "have"],
    "they've": ["they", "have"],
    "theyve": ["they", "have"],
    "i'd": ["i", "would"],
    "you'd": ["you", "would"],
    "we'd": ["we", "would"],
    "they'd": ["they", "would"],
}

IS_WIN = os.name == "nt"
NODE_DIR = HERE.parent / "tools" / "node"
NODE_DIST = "https://nodejs.org/dist/latest-v22.x/"


def _local_node() -> Optional[Path]:
    exe = NODE_DIR / ("node.exe" if IS_WIN else "bin/node")
    return exe if exe.is_file() else None


def node_path() -> str:
    """Resolve the Node.js binary: system `node` on PATH first, else the portable copy in `tools/node`."""
    found = shutil.which("node")
    if found:
        return found
    local = _local_node()
    return str(local) if local else ""


def _npm_cmd() -> Tuple[List[str], Dict[str, str]]:
    """Resolve the `npm` command and an environment with `node` on PATH."""
    env = dict(os.environ)
    sys_npm = shutil.which("npm")
    if sys_npm and shutil.which("node"):
        return [sys_npm], env
    local = _local_node()
    if not local:
        return [], env
    env["PATH"] = str(local.parent) + os.pathsep + env.get("PATH", "")
    npm = NODE_DIR / ("npm.cmd" if IS_WIN else "bin/npm")
    return ([str(npm)] if npm.is_file() else []), env


def install_portable_node(timeout: float = 600.0) -> bool:
    """Download portable Node 22 LTS into `tools/node` after verifying its SHA-256 checksum from `nodejs.org`."""
    import hashlib
    import platform
    import tarfile
    import tempfile
    import urllib.request
    import zipfile

    if _local_node():
        return True
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if IS_WIN:
        suffix = f"-win-{arch}.zip"
    elif os.uname().sysname == "Darwin":
        suffix = f"-darwin-{arch}.tar.gz"
    else:
        suffix = f"-linux-{arch}.tar.gz"
    try:
        with urllib.request.urlopen(NODE_DIST + "SHASUMS256.txt", timeout=30) as resp:  # noqa: S310
            sums = resp.read().decode("utf-8", "replace")
        name, want = "", ""
        for line in sums.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].startswith("node-v") and parts[1].endswith(suffix):
                want, name = parts[0].lower(), parts[1]
                break
        if not name:
            print(f"  [SUBS] nodejs.org lists no Node build for this platform ({suffix.strip('-')})")
            return False
        print(f"  [SUBS] Node.js not found on PATH: downloading {name} into tools/node...")
        tmp = Path(tempfile.mkdtemp(prefix="zeno_node_"))
        arc = tmp / name
        with urllib.request.urlopen(NODE_DIST + name, timeout=timeout) as resp, open(arc, "wb") as fh:  # noqa: S310
            shutil.copyfileobj(resp, fh)
        if hashlib.sha256(arc.read_bytes()).hexdigest().lower() != want:
            print("  [SUBS] Downloaded Node archive failed SHA-256 verification; aborting install.")
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        out = tmp / "x"
        if name.endswith(".zip"):
            with zipfile.ZipFile(arc) as zf:
                zf.extractall(out)
        else:
            with tarfile.open(arc) as tf:
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(out, filter="tar")
                else:
                    tf.extractall(out)  # noqa: S202
        inner = next(p for p in out.iterdir() if p.is_dir() and p.name.startswith("node-v"))
        NODE_DIR.parent.mkdir(parents=True, exist_ok=True)
        if NODE_DIR.exists():
            shutil.rmtree(NODE_DIR, ignore_errors=True)
        shutil.move(str(inner), str(NODE_DIR))
        shutil.rmtree(tmp, ignore_errors=True)
        ok = _local_node() is not None
        print("  [SUBS] Portable Node.js ready in tools/node" if ok else "  [SUBS] Node.js archive could not be unpacked")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"  [SUBS] Could not install portable Node.js ({type(exc).__name__}: {exc}); install Node LTS from nodejs.org.")
        return False


def available() -> bool:
    """Return True when Node.js, `node-canvas`, and `subtitles/styles.json` are all installed."""
    return bool(node_path()) and (SUB_DIR / "node_modules" / "canvas").is_dir() and (SUB_DIR / "styles.json").is_file()


def ensure_ready() -> bool:
    """Ensure Node.js and the `node-canvas` dependency in `subtitles/` are ready for rendering."""
    if available():
        return True
    if not node_path():
        install_portable_node()
    npm, env = _npm_cmd()
    if node_path() and npm and (SUB_DIR / "package.json").is_file() and not (SUB_DIR / "node_modules" / "canvas").is_dir():
        print("  [SUBS] Installing subtitle renderer dependencies (node-canvas)...")
        try:
            r = subprocess.run(
                npm + ["install", "--no-audit", "--no-fund", "--silent"],
                cwd=str(SUB_DIR),
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
                shell=False,
            )
            if r.returncode != 0:
                print("  [SUBS] Subtitle renderer install failed: " + (r.stderr or r.stdout or "").strip()[-300:])
        except Exception as exc:  # noqa: BLE001
            print(f"  [SUBS] Subtitle renderer install error: {exc}")
    return available()


def styles() -> List[Dict[str, Any]]:
    """Load the curated subtitle style catalog from `subtitles/styles.json`."""
    return json.loads((SUB_DIR / "styles.json").read_text(encoding="utf-8"))


# Script/language grouping for bundled static Google Fonts (`subtitles/fonts.json`)
_INDIC_FAMILY_SCRIPTS: Dict[str, Tuple[str, ...]] = {
    # Hindi / Marathi (Devanagari)
    "anek devanagari": ("devanagari", "hindi", "marathi"),
    "baloo 2": ("devanagari", "hindi", "marathi"),
    "hind": ("devanagari", "hindi", "marathi"),
    "kalam": ("devanagari", "hindi", "marathi"),
    "modak": ("devanagari", "hindi", "marathi"),
    "mukta": ("devanagari", "hindi", "marathi"),
    "noto sans devanagari": ("devanagari", "hindi", "marathi"),
    "noto serif devanagari": ("devanagari", "hindi", "marathi"),
    "rozha one": ("devanagari", "hindi", "marathi"),
    "teko": ("devanagari", "hindi", "marathi"),
    # Kannada
    "anek kannada": ("kannada",),
    "baloo tamma 2": ("kannada",),
    "noto sans kannada": ("kannada",),
    "noto serif kannada": ("kannada",),
    "tiro kannada": ("kannada",),
    # Telugu
    "anek telugu": ("telugu",),
    "gidugu": ("telugu",),
    "hind guntur": ("telugu",),
    "noto sans telugu": ("telugu",),
    "noto serif telugu": ("telugu",),
    "ramabhadra": ("telugu",),
    # Tamil
    "anek tamil": ("tamil",),
    "baloo thambi 2": ("tamil",),
    "catamaran": ("tamil",),
    "coiny": ("tamil",),
    "hind madurai": ("tamil",),
    "kavivanar": ("tamil",),
    "mukta malar": ("tamil",),
    "noto sans tamil": ("tamil",),
    # Malayalam
    "anek malayalam": ("malayalam",),
    "baloo chettan 2": ("malayalam",),
    "chilanka": ("malayalam",),
    "manjari": ("malayalam",),
    "noto sans malayalam": ("malayalam",),
    "noto serif malayalam": ("malayalam",),
    # Bengali
    "anek bangla": ("bengali",),
    "atma": ("bengali",),
    "baloo da 2": ("bengali",),
    "galada": ("bengali",),
    "hind siliguri": ("bengali",),
    "noto sans bengali": ("bengali",),
    "noto serif bengali": ("bengali",),
    # Gujarati
    "anek gujarati": ("gujarati",),
    "baloo bhai 2": ("gujarati",),
    "hind vadodara": ("gujarati",),
    "mogra": ("gujarati",),
    "mukta vaani": ("gujarati",),
    "noto sans gujarati": ("gujarati",),
    # Punjabi (Gurmukhi)
    "anek gurmukhi": ("gurmukhi", "punjabi"),
    "baloo paaji 2": ("gurmukhi", "punjabi"),
    "mukta mahee": ("gurmukhi", "punjabi"),
    "noto sans gurmukhi": ("gurmukhi", "punjabi"),
    # Odia (Oriya)
    "anek odia": ("odia", "oriya"),
    "baloo bhaina 2": ("odia", "oriya"),
    "noto sans oriya": ("odia", "oriya"),
    "noto serif oriya": ("odia", "oriya"),
}


def fonts() -> List[Dict[str, Any]]:
    """Load the bundled static font registry from `subtitles/fonts.json`."""
    p = SUB_DIR / "fonts.json"
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def list_fonts(script: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return bundled static font families with available weights and script tags.

    Pass `script` (e.g. `"english"`, `"latin"`, `"hindi"`, `"kannada"`, `"tamil"`,
    `"telugu"`, `"malayalam"`, `"bengali"`, `"gujarati"`, `"punjabi"`, `"odia"`,
    or `"indic"`) to filter the catalog.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for entry in fonts():
        fam = str(entry.get("family") or "").strip()
        fname = str(entry.get("file") or "").strip()
        if not fam or not fname:
            continue
        if not (SUB_DIR / "fonts" / fname).is_file():
            continue
        key = fam.lower()
        if key not in grouped:
            scripts = _INDIC_FAMILY_SCRIPTS.get(key, ("english", "latin"))
            grouped[key] = {
                "family": fam,
                "weights": [],
                "scripts": list(scripts),
                "is_indic": key in _INDIC_FAMILY_SCRIPTS,
            }
        w = int(entry.get("weight") or 400)
        if w not in grouped[key]["weights"]:
            grouped[key]["weights"].append(w)

    items = []
    want = str(script or "").strip().lower()
    for rec in sorted(grouped.values(), key=lambda x: x["family"].lower()):
        rec["weights"].sort()
        if want:
            if want == "indic":
                if not rec["is_indic"]:
                    continue
            elif want in ("english", "latin"):
                if rec["is_indic"]:
                    continue
            elif want not in rec["scripts"]:
                continue
        items.append(rec)
    return items


def resolve_font(token: Any) -> Optional[str]:
    """Resolve a bundled font family name case-insensitively (accepting hyphens/underscores)."""
    if token is None:
        return None
    raw = str(token).strip().strip("\"'")
    if not raw:
        return None
    norm = re.sub(r"[-_\s]+", " ", raw).strip().lower()
    for rec in list_fonts():
        fam = str(rec["family"])
        if fam.lower() == norm or re.sub(r"[-_\s]+", " ", fam).strip().lower() == norm:
            return fam
    return None


def resolve_style(token: Any) -> Optional[Dict[str, Any]]:
    """Resolve a subtitle style by numeric ID (`4`), exact name (`Blue Highlight`), or alias (`Hormozi Punch`)."""
    if token is None:
        return None
    tok = str(token).strip().strip("\"'").lower()
    if not tok:
        return None
    pool = [x for x in styles() for _ in range(max(1, int(x.get("autoWeight", 6))))]
    for s in pool:
        if str(s.get("id")) == tok or str(s.get("name", "")).lower() == tok:
            return s
    norm = re.sub(r"[-_]+", " ", tok)
    norm = re.sub(r"\s+", " ", norm).strip()
    alias_id = STYLE_ALIASES.get(norm)
    if alias_id is not None:
        for s in pool:
            if int(s.get("id", -1)) == alias_id:
                return s
    return None


# ---------------------------------------------------------------------------
# Clip Cache & Aligned Word Loading
# ---------------------------------------------------------------------------
def _safe_stem(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "clip"


def _load_clip_caches_map(work_dir: Optional[Path]) -> Dict[str, str]:
    if work_dir is None:
        return {}
    f = Path(work_dir) / "clip_caches.json"
    if not f.is_file():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(v, str) and v.strip():
                out[str(k)] = v.strip()
            elif isinstance(v, dict):
                cand = str(v.get("cache") or v.get("dir") or v.get("path") or "").strip()
                if cand:
                    out[str(k)] = cand
        return out
    except Exception:
        return {}


def _cache_dirs_for_stem(stem: str) -> List[Path]:
    from common import CLIP_CACHE

    if not CLIP_CACHE.is_dir():
        return []
    s_clean = _safe_stem(stem)
    matches = [d for d in CLIP_CACHE.glob(f"{s_clean}-??????????") if d.is_dir()]
    if not matches:
        prefix_low = f"{s_clean.lower()}-"
        matches = [
            d for d in CLIP_CACHE.iterdir()
            if d.is_dir() and d.name.lower().startswith(prefix_low) and len(d.name) == len(prefix_low) + 10
        ]

    def _dir_rank(d: Path) -> Tuple[int, float]:
        has_words = int(any((d / fn).is_file() for fn in ("hq_words.json", "align.json", "words.json")))
        try:
            mt = d.stat().st_mtime
        except OSError:
            mt = 0.0
        return (has_words, mt)

    return sorted(matches, key=_dir_rank, reverse=True)


def _resolve_clip_cache_dirs(src: Any, work_dir: Optional[Path] = None, include_vo_siblings: bool = False) -> List[Path]:
    from common import CLIP_CACHE, clip_work_dir

    if not src:
        return []
    p = Path(str(src))
    fname = p.name
    stem = p.stem
    seen: Set[Path] = set()
    dirs: List[Path] = []

    def _add(cand: Optional[Path]) -> None:
        if cand is None:
            return
        try:
            rc = cand.resolve()
        except OSError:
            rc = cand
        if rc not in seen and cand.is_dir():
            seen.add(rc)
            dirs.append(cand)

    cache_map = _load_clip_caches_map(work_dir)
    if cache_map:
        lookup_keys = [str(src), p.as_posix(), fname, stem]
        lower_map = {k.lower(): v for k, v in cache_map.items()}
        for key in lookup_keys:
            mapped = cache_map.get(key) or lower_map.get(key.lower())
            if mapped:
                mp = Path(mapped)
                _add(mp if mp.is_absolute() else (CLIP_CACHE / mapped))
                _add(Path(work_dir) / mapped if work_dir else None)

    try:
        wd = clip_work_dir(src)
        _add(wd)
    except Exception:
        wd = None

    if not p.exists() or wd is None or not wd.is_dir():
        for d in _cache_dirs_for_stem(stem):
            _add(d)

    if include_vo_siblings:
        vo_stems = [f"{stem}VO", f"{stem}_VO", f"{stem}-VO", f"{stem} VO", f"{stem}vo", f"{stem}_vo"]
        if cache_map:
            lower_map = {k.lower(): v for k, v in cache_map.items()}
            for vs in vo_stems:
                for key in (vs, f"{vs}{p.suffix}", f"{vs}.mp4", f"{vs}.mov", f"{vs}.wav"):
                    mapped = cache_map.get(key) or lower_map.get(key.lower())
                    if mapped:
                        mp = Path(mapped)
                        _add(mp if mp.is_absolute() else (CLIP_CACHE / mapped))
        if p.parent and str(p.parent) not in ("", "."):
            for vs in vo_stems:
                for ext in (p.suffix, ".mp4", ".mov", ".wav", ".m4a"):
                    if not ext:
                        continue
                    sib = p.parent / f"{vs}{ext}"
                    if sib.exists():
                        try:
                            _add(clip_work_dir(sib))
                        except Exception:
                            pass
        for vs in vo_stems:
            for d in _cache_dirs_for_stem(vs):
                _add(d)

    return dirs


def _normalize_word_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("words")
        if items is None and isinstance(raw.get("segments"), list):
            items = [w for seg in raw["segments"] if isinstance(seg, dict) for w in (seg.get("words") or [])]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for w in items:
        if not isinstance(w, dict):
            continue
        txt = re.sub(r"\s+", " ", str(w.get("word") or w.get("w") or w.get("text") or "")).strip()
        if not txt:
            continue
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws))
        except (TypeError, ValueError):
            continue
        if we < ws:
            continue
        norm = dict(w)
        norm["text"] = txt
        norm["word"] = txt
        norm["w"] = txt
        norm["start"] = ws
        norm["end"] = we if we > ws else ws + 0.04
        out.append(norm)
    return out


def _richest_words_in_dir(d: Path) -> List[Dict[str, Any]]:
    best_words: List[Dict[str, Any]] = []
    best_score = -1.0
    priorities = (("hq_words.json", 1.5), ("align.json", 0.5), ("words.json", 0.0))
    for fname, bonus in priorities:
        f = d / fname
        if not f.is_file():
            continue
        try:
            parsed = json.loads(f.read_text(encoding="utf-8"))
            wlist = _normalize_word_list(parsed)
            if wlist:
                sc = len(wlist) + bonus
                if sc > best_score:
                    best_score = sc
                    best_words = wlist
        except Exception:
            continue
    return best_words


def _words_of_clip(src: Any, work_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the richest aligned word list for `src`, checking `clip_caches.json`, `clip_work_dir`,
    offline `CLIP_CACHE` matches, `("hq_words.json", "align.json", "words.json")`, and `<stem>VO` siblings."""
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=False):
        words = _richest_words_in_dir(d)
        if words:
            return words
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=True):
        words = _richest_words_in_dir(d)
        if words:
            return words
    return []


def _speech_spans(src: Any, work_dir: Optional[Path] = None) -> List[Tuple[float, float]]:
    """Load VAD active speech spans `[(start, end), ...]` for `src` from clip cache."""
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=True):
        f = d / "speech_spans.json"
        if f.is_file():
            try:
                spans = json.loads(f.read_text(encoding="utf-8")).get("spans", [])
                out = [(float(x[0]), float(x[1])) for x in spans if isinstance(x, (list, tuple)) and len(x) >= 2]
                if out:
                    return out
            except Exception:
                continue
    return []


def _rms_envelope_of_clip(src: Any, work_dir: Optional[Path] = None) -> List[Tuple[float, float]]:
    """Load 10ms RMS audio envelope `(time_sec, dbFS)` for `src` from clip cache."""
    try:
        from prepare import compute_10ms_rms_envelope
    except Exception:
        return []
    for inc_vo in (False, True):
        for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=inc_vo):
            wav = d / "audio16k.wav"
            if wav.is_file() and _richest_words_in_dir(d):
                return compute_10ms_rms_envelope(wav)
    for inc_vo in (False, True):
        for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=inc_vo):
            wav = d / "audio16k.wav"
            if wav.is_file():
                return compute_10ms_rms_envelope(wav)
    return []


def _call_clip_loader(fn: Callable[..., Any], src: Any, work_dir: Optional[Path]) -> Any:
    """Invoke `_words_of_clip` or `_speech_spans` while remaining compatible with 1-argument test mocks."""
    if work_dir is None:
        return fn(src)
    try:
        return fn(src, work_dir)
    except TypeError:
        return fn(src)


# ---------------------------------------------------------------------------
# Script Line IDs, Contractions & Foreign-Word Boundary Guard
# ---------------------------------------------------------------------------
def _base_line(seg_id: str) -> str:
    m = re.match(r"^(L\d+)", str(seg_id))
    return m.group(1) if m else str(seg_id)


def _seg_lines(seg_id: str) -> Set[str]:
    """Resolve `L1`, `L1b`, `L1_L2` (`{"L1", "L2"}`), or ranges `L2_L5` (`{"L2", ..., "L5"}`);
    returns an empty set for custom non-line IDs (`S1`, `HOOK1`) so custom IDs never filter out spoken words."""
    s = str(seg_id or "").strip()
    if not re.match(r"^L\d+", s, re.IGNORECASE):
        return set()
    m_range = re.match(r"^L(\d+)[a-z]*(?:_|-|\.\.)L(\d+)[a-z]*$", s, re.IGNORECASE)
    if m_range:
        a, b = int(m_range.group(1)), int(m_range.group(2))
        if a <= b and (b - a) <= 40:
            return {f"L{k}" for k in range(a, b + 1)}
    nums = [int(x) for x in re.findall(r"L(\d+)", s, re.IGNORECASE)]
    return {f"L{n}" for n in nums}


def _normalize_apostrophes(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").replace("`", "'")


def _expand_word_forms(word: str) -> List[List[str]]:
    """Return candidate tokenizations for `word`: `[cleaned_single]` and (when contracted) `[expanded_parts]`."""
    from script_order import clean

    w_norm = _normalize_apostrophes(str(word or "").strip().lower())
    w_trim = re.sub(r"^[^a-z0-9\u0900-\u0D7F']+|[^a-z0-9\u0900-\u0D7F']+$", "", w_norm)
    forms: List[List[str]] = []
    base = clean([w_norm])
    if base:
        forms.append(base)
    if w_trim and w_trim not in (base[0] if base else ""):
        forms.append([w_trim])
    no_ap = w_trim.replace("'", "")
    for key in (w_trim, no_ap):
        if key in CONTRACTIONS:
            exp = CONTRACTIONS[key]
            if exp not in forms:
                forms.append(list(exp))
    return forms


def _build_line_token_map(script_lines: Optional[List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    from script_order import clean

    out: Dict[str, List[str]] = {}
    for line_entry in script_lines or []:
        lid = str(line_entry.get("id") or "").strip()
        if not lid:
            continue
        acc: List[str] = []
        seen: Set[str] = set()

        def _push(t: str) -> None:
            if t and t not in seen:
                seen.add(t)
                acc.append(t)

        for tok in line_entry.get("tokens") or []:
            s_tok = _normalize_apostrophes(str(tok).strip().lower())
            _push(s_tok)
            for c_tok in clean([s_tok]):
                _push(c_tok)
            for key in (s_tok, s_tok.replace("'", "")):
                for exp_tok in CONTRACTIONS.get(key, ()):
                    _push(exp_tok)

        raw_text = str(line_entry.get("text") or "").strip()
        if raw_text:
            for raw_w in raw_text.split():
                for form in _expand_word_forms(raw_w):
                    for part in form:
                        _push(part)

        if acc:
            out[lid] = acc
    return out


def _word_matches_tokens(word: str, target_tokens: List[str], similarity: Callable[[str, str], float], threshold: float = 0.78) -> bool:
    if not target_tokens:
        return False
    forms = _expand_word_forms(word)
    if not forms:
        return False
    for form in forms:
        if all(any(similarity(part, cand) >= threshold for cand in target_tokens) for part in form):
            return True
    return False


def _foreign_to_line(
    word: str,
    seg_line: Any,
    line_tokens: Dict[str, List[str]],
    similarity: Callable[[str, str], float],
    adjacent_lines: Optional[Set[str]] = None,
) -> bool:
    """Return True when `word` does not match the segment's target line(s) and strongly (`>= 0.80`) matches an adjacent script line."""
    if isinstance(seg_line, (set, frozenset, list, tuple)):
        target_ids = {str(x) for x in seg_line if str(x)}
    else:
        target_ids = _seg_lines(str(seg_line))
    if not target_ids or not line_tokens:
        return False

    target_toks = [tok for lid in target_ids for tok in line_tokens.get(lid, [])]
    if _word_matches_tokens(word, target_toks, similarity, threshold=0.78):
        return False

    check_ids = adjacent_lines if adjacent_lines is not None else (set(line_tokens.keys()) - target_ids)
    for lid in check_ids:
        if lid in target_ids:
            continue
        other_toks = line_tokens.get(lid, [])
        if other_toks and _word_matches_tokens(word, other_toks, similarity, threshold=0.80):
            return True
    return False


def _adjacent_line_ids(target_ids: Set[str], line_tokens: Dict[str, List[str]]) -> Tuple[Set[str], Set[str]]:
    nums = sorted(int(lid[1:]) for lid in target_ids if lid.startswith("L") and lid[1:].isdigit())
    if not nums:
        return set(), set()
    lo, hi = nums[0], nums[-1]
    avail = sorted((int(lid[1:]), lid) for lid in line_tokens if lid.startswith("L") and lid[1:].isdigit())
    prev_ids: Set[str] = {f"L{lo - 1}"} if lo > 1 else set()
    next_ids: Set[str] = {f"L{hi + 1}"}
    below = [lid for n, lid in avail if n < lo]
    above = [lid for n, lid in avail if n > hi]
    if below:
        prev_ids.add(below[-1])
    if above:
        next_ids.add(above[0])
    return prev_ids, next_ids


# ---------------------------------------------------------------------------
# Script-Line + Speech-Span Synthesis Fallback
# ---------------------------------------------------------------------------
def _script_words_for_lines(target_ids: Set[str], script_lines: Optional[List[Dict[str, Any]]]) -> List[str]:
    if not target_ids or not script_lines:
        return []
    ordered_ids = sorted(target_ids, key=lambda x: int(x[1:]) if x.startswith("L") and x[1:].isdigit() else 999999)
    by_id = {str(line_entry.get("id")): line_entry for line_entry in script_lines if line_entry.get("id")}
    words: List[str] = []
    for lid in ordered_ids:
        entry = by_id.get(lid)
        if not entry:
            continue
        txt = str(entry.get("text") or "").strip()
        if txt:
            cleaned = re.sub(r"\[[^\]]*\]", " ", txt)
            words.extend(w for w in cleaned.split() if w.strip())
        elif entry.get("tokens"):
            words.extend(str(t) for t in entry["tokens"] if str(t).strip())
    return words


def _active_speech_windows(st: float, en: float, spoken: List[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], float]:
    dur = max(0.0, en - st)
    if dur <= 0.04:
        return [], 0.0
    if spoken:
        clipped = [(max(st, a), min(en, b)) for a, b in spoken if min(en, b) - max(st, a) >= 0.04]
        clipped.sort(key=lambda x: x[0])
        merged: List[Tuple[float, float]] = []
        for a, b in clipped:
            if merged and a - merged[-1][1] <= 0.14:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        if merged:
            return merged, sum(b - a for a, b in merged)
        # VAD-missed quiet speech fallback on non-muted spoken cuts
        if dur >= 0.35:
            return [(st, en)], dur
        return [], 0.0
    if dur >= 0.25:
        pad = min(0.05, dur * 0.08)
        win = (st + pad, en - pad)
        return [win], max(0.0, win[1] - win[0])
    return [], 0.0


def _synthesize_words_over_spans(
    words_text: List[str],
    tl_spans: List[Tuple[int, float, float]],
) -> Dict[int, List[Dict[str, Any]]]:
    """Distribute `words_text` proportionally across `(seg_idx, tl_start, tl_end)` speech windows."""
    result: Dict[int, List[Dict[str, Any]]] = {}
    if not words_text or not tl_spans:
        return result
    total_dur = sum(max(0.02, b - a) for _, a, b in tl_spans)
    if total_dur <= 0.02:
        return result

    weights: List[float] = []
    for w in words_text:
        base_w = max(2.0, float(sum(1 for ch in w if unicodedata.category(ch) != "Mn")))
        if SENTENCE_END.search(w):
            base_w += 1.6
        elif CLAUSE_END.search(w):
            base_w += 0.9
        weights.append(base_w)

    n_words = len(words_text)
    n_spans = len(tl_spans)
    cum_dur = 0.0
    start_idx = 0
    for s_pos, (seg_idx, sp_a, sp_b) in enumerate(tl_spans):
        cum_dur += max(0.02, sp_b - sp_a)
        if s_pos == n_spans - 1:
            end_idx = n_words
        else:
            target_cum = round(n_words * (cum_dur / total_dur))
            end_idx = max(start_idx, min(n_words, int(target_cum)))
        chunk = words_text[start_idx:end_idx]
        chunk_weights = weights[start_idx:end_idx]
        start_idx = end_idx
        if not chunk:
            continue
        span_len = max(0.04, sp_b - sp_a)
        w_sum = sum(chunk_weights) or float(len(chunk))
        cursor = sp_a
        seg_out = result.setdefault(seg_idx, [])
        for k, w_txt in enumerate(chunk):
            w_dur = span_len * (chunk_weights[k] / w_sum)
            w_end = sp_b if k == len(chunk) - 1 else min(sp_b, cursor + max(0.04, w_dur))
            seg_out.append({"text": w_txt, "start": cursor, "end": max(cursor + 0.03, w_end)})
            cursor = w_end
    return result


# ---------------------------------------------------------------------------
# Multilingual Phrase Chunking & Strictly Monotonic Cues
# ---------------------------------------------------------------------------
def _visual_units(text: str) -> float:
    """Estimate horizontal rendered width of `text` in normalized character units (~40px at 54px font size).
    Accounts for wider Tamil/Malayalam glyphs (+30%), Devanagari/Bengali/Gurmukhi/Gujarati/Odia/Telugu/Kannada
    glyphs (+15%), narrow lowercase Latin (-20%), and non-spacing/spacing Indic vowel signs."""
    units = 0.0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Mn":
            continue
        cp = ord(ch)
        if 0x0B80 <= cp <= 0x0BFF or 0x0D00 <= cp <= 0x0D7F:      # Tamil, Malayalam
            w = 1.30
        elif (
            0x0900 <= cp <= 0x097F                                 # Devanagari
            or 0x0980 <= cp <= 0x09FF                              # Bengali
            or 0x0A00 <= cp <= 0x0A7F                              # Gurmukhi
            or 0x0A80 <= cp <= 0x0AFF                              # Gujarati
            or 0x0B00 <= cp <= 0x0B7F                              # Odia
            or 0x0C00 <= cp <= 0x0C7F                              # Telugu
            or 0x0C80 <= cp <= 0x0CFF                              # Kannada
        ):
            w = 1.15
        elif cp < 0x0250:                                           # Latin
            w = 1.0 if (ch.isupper() or ch.isdigit()) else (0.45 if ch in "il.,'!|:;" else 0.80)
        else:
            w = 1.0
        units += w * (0.5 if cat == "Mc" else 1.0)
    return units


# Multilingual function words, determiners, postpositions, and light-verb stems across
# Kannada, Telugu, Tamil, Malayalam, Hindi, Marathi, Bengali, and English so caption cards
# break cleanly at natural phrase boundaries rather than stranding numbers, possessives, or auxiliaries.
_NO_END = {w.lower() for line in (
    "ನನ್ನ ನಿನ್ನ ನಿಮ್ಮ ನಮ್ಮ ಅವನ ಅವಳ ಅವರ ಈ ಆ ಒಂದು ಮತ್ತೆ ಹಾಗೂ ಆದ್ರೆ ಆದರೆ ಯಾವ ಎಷ್ಟು",
    "నా నీ మీ మా ఈ ఆ ఒక మరియు కానీ",
    "என் உன் உங்கள் எங்கள் இந்த அந்த ஒரு மற்றும் ஆனால்",
    "എന്റെ നിന്റെ നിങ്ങളുടെ ഞങ്ങളുടെ ഈ ആ ഒരു എന്നാൽ",
    "मेरा मेरी मेरे तेरा तेरी तुम्हारा तुम्हारी आपका आपकी आपके हमारा हमारी इस उस एक और या कि लेकिन पर जो",
    "माझा माझी माझे तुझा तुझी तुमचा तुमची तुमचे आमचा आमची हा ही हे तो ती आणि पण",
    "আমার তোমার আপনার আমাদের এই ওই এক এবং কিন্তু",
    "a an the to of in on at for with my your our his her their and or but that this these those is are was were be",
) for w in line.split()}

_NO_START = {w.lower() for line in (
    "का की के को में से पर ने तक भी ही है हैं था थी थे हूँ हो हूं",
    "ला चा ची चे ना त ही आहे आहेत होता होती",
    "এর কে তে থেকে ও ই আছে হয় করে",
) for w in line.split()}

_NO_START_PREFIX = (
    "ಮಾಡ", "ಆಗ", "ಇದ್ದ", "ಇದೆ", "ಇರು", "ಕೊಡ",
    "చేయ", "చేస", "అవ",
    "ചെയ്", "ആയി",
    "ஆகு", "செய்",
    "করি", "করে", "করব", "করছ", "হবে", "হচ্ছ",
)


def _core(w: str) -> str:
    """Strip leading/trailing punctuation/symbol/separator characters while preserving Indic combining vowel signs."""
    t = w or ""
    i, j = 0, len(t)
    while i < j and unicodedata.category(t[i])[0] in "PSZ":
        i += 1
    while j > i and unicodedata.category(t[j - 1])[0] in "PSZ":
        j -= 1
    return t[i:j].lower()


def _is_no_end(text: str) -> bool:
    c = _core(text)
    return bool(c) and (c.isdigit() or c in _NO_END)


def _is_no_start(text: str) -> bool:
    c = _core(text)
    return bool(c) and (c in _NO_START or c.startswith(_NO_START_PREFIX))


def _chunk_continuous_phrase(run_words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Partition an unbroken speech run into single-row caption cards via dynamic programming:
    rewards breaks after clause/sentence punctuation, penalizes orphan 1-word cards, numbers split
    from units, possessives/prepositions at card ends, and postpositions/light verbs at card starts."""
    n = len(run_words)
    if n == 0:
        return []
    has_indic = any(INDIC_RE.search(w["text"]) for w in run_words)
    max_w = MAX_WORDS_PER_CUE
    units = [_visual_units(w["text"]) for w in run_words]
    inf = float("inf")
    best = [inf] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        width = 0.0
        for i in range(j - 1, max(-1, j - max_w - 1), -1):
            width += units[i] + (1.0 if i < j - 1 else 0.0)
            if j - i > 1 and width > MAX_VISUAL_UNITS:
                break
            if has_indic and j - i > MAX_INDIC_WORDS_PER_CUE and width > 16.0:
                continue
            if best[i] == inf:
                continue
            size = j - i
            cost = 1.0 + (1.6 if size == 1 and n > 1 else 0.0) + (0.25 if size == 2 and n > 3 else 0.0)
            if j < n:
                last, nxt = run_words[j - 1]["text"], run_words[j]["text"]
                if CLAUSE_END.search(last) or SENTENCE_END.search(last):
                    cost -= 2.5
                if _is_no_end(last):
                    cost += 7.0
                if _is_no_start(nxt):
                    cost += 7.0
                # Keep code-switched English nouns paired with trailing Indic inflections/auxiliaries
                if (
                    INDIC_RE.search(nxt)
                    and not INDIC_RE.search(last)
                    and re.search(r"[A-Za-z]", last)
                    and not (CLAUSE_END.search(last) or SENTENCE_END.search(last))
                ):
                    cost += 0.6
            cost += (1.0 - min(1.0, width / MAX_VISUAL_UNITS)) * 0.4
            if best[i] + cost < best[j]:
                best[j] = best[i] + cost
                prev[j] = i
    if best[n] == inf:
        return [[w] for w in run_words]
    chunks: List[List[Dict[str, Any]]] = []
    j = n
    while j > 0:
        i = prev[j]
        chunks.append(run_words[i:j])
        j = i
    chunks.reverse()
    return [c for c in chunks if c]


def _group_segment_words(words: List[Dict[str, Any]], gap_scale: float = 1.0) -> List[List[Dict[str, Any]]]:
    if not words:
        return []
    eff_scale = max(1.0, float(gap_scale or 1.0))
    eff_max_gap = MAX_GAP_IN_CUE * eff_scale
    eff_glue_gap = 0.75 * eff_scale
    runs: List[List[Dict[str, Any]]] = []
    cur_run: List[Dict[str, Any]] = []
    for w in words:
        if cur_run:
            gap = w["start"] - cur_run[-1]["end"]
            glued = (
                gap <= eff_glue_gap
                and (_is_no_end(cur_run[-1]["text"]) or _is_no_start(w["text"]))
                and not SENTENCE_END.search(cur_run[-1]["text"])
            )
            if (gap > eff_max_gap and not glued) or SENTENCE_END.search(cur_run[-1]["text"]):
                runs.append(cur_run)
                cur_run = []
        cur_run.append(w)
    if cur_run:
        runs.append(cur_run)

    groups: List[List[Dict[str, Any]]] = []
    for r in runs:
        groups.extend(_chunk_continuous_phrase(r))
    return groups


def _cue(words: List[Dict[str, Any]], max_end: Optional[float] = None) -> Dict[str, Any]:
    start_t = words[0]["start"]
    raw_end = max(w["end"] for w in words) + 0.04
    if max_end is not None:
        raw_end = min(raw_end, max_end)
    end_t = max(start_t + 0.04, raw_end)
    cue_words = []
    clean_texts = []
    for w in words:
        ws = max(start_t, w["start"])
        we = min(end_t, max(ws + 0.02, w["end"]))
        w_txt = re.sub(r"\s+", " ", str(w.get("text") or w.get("word") or w.get("w") or "")).strip()
        cue_words.append({"text": w_txt, "startTime": round(ws, 3), "endTime": round(we, 3)})
        if w_txt:
            clean_texts.append(w_txt)
    return {
        "startTime": round(start_t, 3),
        "endTime": round(end_t, 3),
        "text": " ".join(clean_texts),
        "words": cue_words,
    }


def _segment_speed(seg: Dict[str, Any]) -> float:
    """Return clamped playback speed factor via `common.segment_speed` with safe fallback."""
    try:
        import common

        fn = getattr(common, "segment_speed", None)
        if callable(fn):
            val = float(fn(seg))
            if math.isfinite(val) and val > 0.0:
                return val
    except Exception:
        pass
    if not isinstance(seg, dict):
        return 1.0
    raw = seg.get("speed")
    if raw is None or isinstance(raw, bool):
        return 1.0
    try:
        val = float(str(raw).strip().rstrip("xX"))
        return max(0.25, min(4.0, val)) if math.isfinite(val) and val > 0.0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _segment_is_muted(seg: Dict[str, Any]) -> bool:
    """Return True when `seg` disables source audio (`common.segment_is_muted` with safe fallback)."""
    try:
        import common

        fn = getattr(common, "segment_is_muted", None)
        if callable(fn):
            return bool(fn(seg))
    except Exception:
        pass
    if not isinstance(seg, dict):
        return False
    for key in ("mute", "muted"):
        raw = seg.get(key)
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("", "0", "false", "no", "off", "none", "null"):
                continue
            return True
        if raw:
            return True
    am = str(seg.get("audio_mode") or seg.get("audio") or "").strip().lower()
    return am in ("mute", "muted", "off", "none")


def _segment_is_beat(seg: Dict[str, Any]) -> bool:
    """Return True when `seg` is a non-spoken visual beat (`common.segment_is_beat` with safe fallback)."""
    try:
        import common

        fn = getattr(common, "segment_is_beat", None)
        if callable(fn):
            return bool(fn(seg))
    except Exception:
        pass
    if not isinstance(seg, dict):
        return False
    for key in ("beat", "silent"):
        raw = seg.get(key)
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("", "0", "false", "no", "off", "none", "null"):
                continue
            return True
        if raw:
            return True
    return False


def _segment_hold_tail(seg: Dict[str, Any]) -> float:
    """Return non-negative post-speech freeze-hold tail duration in seconds."""
    try:
        import common

        fn = getattr(common, "segment_hold_tail", None)
        if callable(fn):
            return max(0.0, float(fn(seg)))
    except Exception:
        pass
    try:
        return max(0.0, min(1.5, float(seg.get("hold_tail") or seg.get("hold") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _segment_play_duration(seg: Dict[str, Any]) -> float:
    """Return moving playback duration (`(end - start) / speed`, excluding `hold_tail`)."""
    try:
        import common

        fn = getattr(common, "segment_play_duration", None)
        if callable(fn):
            return max(0.0, float(fn(seg)))
    except Exception:
        pass
    src_dur = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
    return src_dur / _segment_speed(seg)


def _src_to_seg_time(seg: Dict[str, Any], src_t: float) -> float:
    """Map absolute source timestamp `src_t` to clamped segment-relative playback seconds `[0, play_dur]`."""
    play_dur = _segment_play_duration(seg)
    try:
        import common

        fn = getattr(common, "src_to_seg_time", None)
        if callable(fn):
            return max(0.0, min(play_dur, float(fn(seg, src_t))))
    except Exception:
        pass
    st = float(seg.get("start", 0.0))
    spd = _segment_speed(seg)
    return max(0.0, min(play_dur, (float(src_t) - st) / spd))


def segment_duration(seg: Dict[str, Any]) -> float:
    """Return total segment duration including speed scaling and any post-speech `hold_tail`."""
    try:
        import common

        fn = getattr(common, "segment_duration", None)
        if callable(fn) and (callable(getattr(common, "segment_play_duration", None)) or abs(_segment_speed(seg) - 1.0) < 1e-4):
            return float(fn(seg))
    except Exception:
        pass
    return _segment_play_duration(seg) + _segment_hold_tail(seg)


def _flag_truthy(raw: Any) -> bool:
    """Return True for truthy booleans/numbers and non-false string flags (`on`/`true`/`yes`/`1`)."""
    if isinstance(raw, str):
        return raw.strip().lower() not in ("", "0", "false", "no", "off", "none", "null")
    return bool(raw)


def _plan_cards_off(plan: Optional[Dict[str, Any]]) -> bool:
    """Return True when the job-level plan disables flashcards (`#cards off`)."""
    if not isinstance(plan, dict):
        return False
    return _flag_truthy(plan.get("cards_off")) or plan.get("cards") is False or plan.get("flashcards") is False


def _segment_has_active_flashcard(seg: Dict[str, Any], plan_cards_off: bool = False) -> bool:
    """Return True when `seg` renders an active flashcard overlay."""
    if not isinstance(seg, dict) or plan_cards_off:
        return False
    if _flag_truthy(seg.get("cards_off")) or seg.get("cards") is False or seg.get("flashcards") is False:
        return False
    return bool(seg.get("flashcards"))


def _segment_subs_off(seg: Dict[str, Any], plan_cards_off: bool = False, with_cards: bool = False) -> bool:
    """Return True when bottom subtitles should be suppressed on `seg`.

    Suppresses whenever `subs_off` (or `subs=off`) is set, or when the segment carries an active
    flashcard unless explicitly opted back in via `subs_on` (`subs=on`) or job-level `with_cards`.
    """
    if not isinstance(seg, dict):
        return False
    if _flag_truthy(seg.get("subs_off")):
        return True
    raw_subs = seg.get("subs")
    if raw_subs is False or (isinstance(raw_subs, str) and raw_subs.strip().lower() in ("off", "none", "false", "no", "0")):
        return True
    if _segment_has_active_flashcard(seg, plan_cards_off=plan_cards_off):
        subs_on = _flag_truthy(seg.get("subs_on")) or (
            raw_subs is True or (isinstance(raw_subs, str) and raw_subs.strip().lower() in ("on", "true", "yes", "1"))
        )
        if not (subs_on or with_cards):
            return True
    return False


def _seg_inline_words(seg: Dict[str, Any]) -> List[str]:
    """Extract spoken caption words directly from a segment's inline `text` or `dialogue` field."""
    if not isinstance(seg, dict):
        return []
    raw = str(seg.get("text") or seg.get("dialogue") or "").strip()
    if not raw:
        return []
    cleaned = re.sub(r"\[[^\]]*\]", " ", raw)
    return [w for w in cleaned.split() if w.strip()]


def _segment_tl_speech_spans(
    seg_idx: int,
    seg: Dict[str, Any],
    tl_bounds: Tuple[float, float],
    spoken: List[Tuple[float, float]],
) -> Tuple[List[Tuple[int, float, float]], float]:
    """Map a segment's active speech windows onto timeline coordinates `(seg_idx, tl_start, tl_end)`."""
    st_k, en_k = float(seg["start"]), float(seg["end"])
    t0_k, tl_end_k = tl_bounds
    play_k = _segment_play_duration(seg)
    seg_len_k = max(0.04, tl_end_k - t0_k)
    max_play_k = min(seg_len_k, play_k) if play_k > 0.0 else seg_len_k
    wins, sp_dur = _active_speech_windows(st_k, en_k, spoken)
    tl_spans: List[Tuple[int, float, float]] = []
    if sp_dur >= 0.18:
        for wa, wb in wins:
            ta = t0_k + _src_to_seg_time(seg, wa)
            tb = min(t0_k + max_play_k, max(ta + 0.04, t0_k + _src_to_seg_time(seg, wb)))
            if tb - ta >= 0.04:
                tl_spans.append((seg_idx, ta, tb))
    return tl_spans, sp_dur


def _cut_is_silent_in_env(
    st: float,
    en: float,
    spoken: List[Tuple[float, float]],
    env: List[Tuple[float, float]],
    floor_db: float = -55.0,
) -> bool:
    """Return True when a measured RMS envelope shows pure silence (`< floor_db`) and no VAD speech overlaps `[st, en]`."""
    if not env:
        return False
    if spoken and any(min(en, b) - max(st, a) >= 0.04 for a, b in spoken):
        return False
    return max((db for t, db in env if st <= t <= en), default=-140.0) < floor_db


def build_cues(
    segs: List[Dict[str, Any]],
    starts: List[float],
    script_lines: Optional[List[Dict[str, Any]]] = None,
    work_dir: Optional[Path] = None,
    plan: Optional[Dict[str, Any]] = None,
    with_cards: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Build strictly monotonic timeline caption cues (`cue[k].endTime <= cue[k+1].startTime`)
    with 2-4 words per card (up to 3 for Indic scripts) and zero blank dialogue segments."""
    try:
        from prepare import _word_similarity as similarity
    except Exception:  # noqa: BLE001
        similarity = lambda a, b: 1.0 if a == b else 0.0  # noqa: E731

    plan_cards_off = _plan_cards_off(plan)
    if with_cards is not None:
        eff_with_cards = _flag_truthy(with_cards)
    elif isinstance(plan, dict) and isinstance(plan.get("subs"), dict):
        eff_with_cards = _flag_truthy(plan["subs"].get("with_cards"))
    else:
        eff_with_cards = False

    line_tokens = _build_line_token_map(script_lines)
    n_segs = len(segs)
    seg_words_list: List[List[Dict[str, Any]]] = [[] for _ in range(n_segs)]
    seg_spoken_list: List[List[Tuple[float, float]]] = [[] for _ in range(n_segs)]
    seg_tl_bounds: List[Tuple[float, float]] = []
    clip_env_cache: Dict[str, List[Tuple[float, float]]] = {}

    # Track straddling boundary words emitted at the tail of a cut so adjacent cuts of the same clip never duplicate them
    prev_clip_src: Optional[str] = None
    prev_clip_en: float = -999.0
    prev_emitted_keys: Dict[Tuple[str, float, float], float] = {}

    for i, s in enumerate(segs):
        st, en = float(s["start"]), float(s["end"])
        t0 = float(starts[i])
        tl_end = float(starts[i + 1]) if i + 1 < len(starts) else t0 + max(0.0, segment_duration(s))
        seg_tl_bounds.append((t0, tl_end))

        if _segment_is_beat(s) or _segment_is_muted(s) or _segment_subs_off(s, plan_cards_off=plan_cards_off, with_cards=eff_with_cards):
            prev_clip_src = None
            prev_emitted_keys = {}
            continue

        src = s.get("src")
        src_key = str(src or "")
        spoken = _call_clip_loader(_speech_spans, src, work_dir)
        seg_spoken_list[i] = spoken
        target_ids = _seg_lines(str(s.get("id") or ""))
        target_toks = [tok for lid in target_ids for tok in line_tokens.get(lid, [])]

        same_contiguous_clip = (
            bool(src_key)
            and src_key == prev_clip_src
            and st < prev_clip_en + 0.15
            and st >= prev_clip_en - 0.50
        )
        if not same_contiguous_clip:
            prev_emitted_keys = {}

        next_same_clip = False
        next_st, next_en = 0.0, 0.0
        if (
            i + 1 < n_segs
            and not (_segment_is_beat(segs[i + 1]) or _segment_is_muted(segs[i + 1]))
            and str(segs[i + 1].get("src") or "") == src_key
        ):
            next_st, next_en = float(segs[i + 1]["start"]), float(segs[i + 1]["end"])
            if next_st < en + 0.15 and next_st >= st:
                next_same_clip = True

        clip_words = _call_clip_loader(_words_of_clip, src, work_dir)
        env = clip_env_cache.get(src_key)
        if env is None:
            env = _call_clip_loader(_rms_envelope_of_clip, src, work_dir) if src else []
            clip_env_cache[src_key] = env
        silent_word_ids: Set[int] = set()
        if clip_words and env:
            try:
                from prepare import words_in_silence

                silent_word_ids = {id(sw) for sw in words_in_silence(clip_words, env)}
            except Exception:
                silent_word_ids = set()

        candidates: List[Dict[str, Any]] = []
        for w in clip_words:
            if w.get("no_sound") or id(w) in silent_word_ids:
                continue
            text = re.sub(r"\s+", " ", str(w.get("w") or w.get("word") or w.get("text") or "")).strip()
            if not text:
                continue
            ws_raw, we_raw = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            if we_raw < ws_raw:
                continue
            dur_w = max(0.001, we_raw - ws_raw)
            mid = (ws_raw + we_raw) / 2.0
            ov = max(0.0, min(we_raw, en) - max(ws_raw, st))
            min_ov = max(0.06, min(0.12, 0.45 * dur_w))
            if not ((st <= mid <= en) or ov >= min_ov):
                continue

            raw_key = (text.lower(), round(ws_raw, 3), round(we_raw, 3))
            if same_contiguous_clip and raw_key in prev_emitted_keys:
                continue
            if next_same_clip and we_raw > next_st and ws_raw < en:
                ov_next = max(0.0, min(we_raw, next_en) - max(ws_raw, next_st))
                if ov_next > ov + 0.015:
                    continue

            matches_target = _word_matches_tokens(text, target_toks, similarity, threshold=0.78) if target_toks else False

            ws, we = ws_raw, we_raw
            if spoken:
                vad_ov = sum(max(0.0, min(we, b) - max(ws, a)) for a, b in spoken)
                near_vad = any(a - 0.05 <= mid <= b + 0.05 for a, b in spoken)
                if vad_ov < 0.04 and not near_vad:
                    near_any_speech = any(a - 0.35 <= mid <= b + 0.35 for a, b in spoken)
                    if not (matches_target and near_any_speech):
                        continue
                # Clamp silence-stretched alignment tails (> 0.65s) to active speech end + 0.08s
                if we - ws > 0.65:
                    ov_spans = sorted(((a, b) for a, b in spoken if min(we, b) > max(ws, a)), key=lambda x: x[0])
                    if ov_spans:
                        span_end = ov_spans[0][1]
                        for a_nx, b_nx in ov_spans[1:]:
                            if a_nx - span_end <= 0.12:
                                span_end = max(span_end, b_nx)
                            else:
                                break
                        if ws + 0.08 < span_end + 0.08 < we:
                            we = span_end + 0.08

            candidates.append({
                "text": text,
                "ws_raw": ws_raw,
                "we_raw": we_raw,
                "ws": ws,
                "we": we,
                "ov": ov,
                "raw_key": raw_key,
                "matches_target": matches_target,
            })

        candidates.sort(key=lambda x: (x["ws"], x["we"]))

        # Edge-only foreign-word guard: inspect only leading/trailing cut-edge words outside [first_target..last_target]
        if candidates and target_ids and line_tokens:
            prev_ids, next_ids = _adjacent_line_ids(target_ids, line_tokens)
            active_wins, _ = _active_speech_windows(st, en, spoken)
            speech_start = active_wins[0][0] if active_wins else st
            speech_end = active_wins[-1][1] if active_wins else en

            target_positions = [idx for idx, c in enumerate(candidates) if c["matches_target"]]
            first_t = target_positions[0] if target_positions else len(candidates)
            last_t = target_positions[-1] if target_positions else -1

            filtered: List[Dict[str, Any]] = []
            for idx, c in enumerate(candidates):
                if first_t <= idx <= last_t:
                    filtered.append(c)
                    continue
                is_lead_edge = idx < first_t and ((c["ws_raw"] - st) < 0.65 or (c["ws_raw"] - speech_start) < 0.65)
                is_tail_edge = idx > last_t and ((en - c["we_raw"]) < 0.65 or (speech_end - c["we_raw"]) < 0.65)
                if is_lead_edge and prev_ids and _foreign_to_line(c["text"], target_ids, line_tokens, similarity, adjacent_lines=prev_ids):
                    continue
                if is_tail_edge and next_ids and _foreign_to_line(c["text"], target_ids, line_tokens, similarity, adjacent_lines=next_ids):
                    continue
                filtered.append(c)
            candidates = filtered

        emitted_keys: Dict[Tuple[str, float, float], float] = {}
        seg_out: List[Dict[str, Any]] = []
        seg_len = max(0.04, tl_end - t0)
        play_len = _segment_play_duration(s)
        max_play = min(seg_len, play_len) if play_len > 0.0 else seg_len
        for c in candidates:
            rel_s = _src_to_seg_time(s, c["ws"])
            rel_e = min(max_play, max(rel_s + 0.02, _src_to_seg_time(s, c["we"])))
            tl_s = max(t0, min(t0 + max_play - 0.02, t0 + rel_s))
            tl_e = min(t0 + max_play, max(tl_s + 0.02, t0 + rel_e))
            seg_out.append({"text": c["text"], "start": tl_s, "end": tl_e, "seg_idx": i})
            emitted_keys[c["raw_key"]] = c["ov"]

        seg_words_list[i] = seg_out
        prev_clip_src = src_key
        prev_clip_en = en
        prev_emitted_keys = emitted_keys

    # Backfill dialogue segments where aligned word coverage is missing (0 words or < 65% of expected).
    # Evaluate per individual non-muted spoken segment so blank sub-cuts (`L7b`) never hide behind `L7a`,
    # and also synthesize captions for custom non-`L<n>` segments (`HOOK`, `CTA1`, `S1`) carrying inline text/dialogue.
    processed_backfill: Set[int] = set()
    for idx in range(n_segs):
        if idx in processed_backfill:
            continue
        s = segs[idx]
        if _segment_is_beat(s) or _segment_is_muted(s):
            continue

        target_ids = _seg_lines(str(s.get("id") or ""))
        if not target_ids:
            processed_backfill.add(idx)
            if _segment_subs_off(s, plan_cards_off=plan_cards_off, with_cards=eff_with_cards):
                continue
            if _cut_is_silent_in_env(float(s["start"]), float(s["end"]), seg_spoken_list[idx], clip_env_cache.get(str(s.get("src") or ""), [])):
                continue
            if len(seg_words_list[idx]) == 0:
                inline_words = _seg_inline_words(s)
                if inline_words:
                    seg_spans, seg_sp_dur = _segment_tl_speech_spans(idx, s, seg_tl_bounds[idx], seg_spoken_list[idx])
                    if not seg_spans:
                        t0_k, tl_end_k = seg_tl_bounds[idx]
                        play_k = _segment_play_duration(s)
                        seg_len_k = max(0.0, tl_end_k - t0_k)
                        max_play_k = min(seg_len_k, play_k) if play_k > 0.0 else seg_len_k
                        if max_play_k >= 0.18:
                            seg_spans = [(idx, t0_k, t0_k + max_play_k)]
                            seg_sp_dur = max_play_k
                    if seg_sp_dur >= 0.18 and seg_spans:
                        synthesized = _synthesize_words_over_spans(inline_words, seg_spans)
                        seg_words_list[idx] = synthesized.get(idx, [])
            continue

        # Collect spoken segments in the same line-ID run (allowing intervening muted/beat cutaways)
        run_indices: List[int] = [idx]
        scan = idx + 1
        while scan < n_segs:
            sn = segs[scan]
            if _segment_is_beat(sn) or _segment_is_muted(sn):
                scan += 1
                continue
            if _seg_lines(str(sn.get("id") or "")) == target_ids:
                run_indices.append(scan)
                scan += 1
            else:
                break
        processed_backfill.update(run_indices)

        expected = _script_words_for_lines(target_ids, script_lines) if script_lines else []
        if not expected:
            for k in run_indices:
                sk = segs[k]
                if _segment_subs_off(sk, plan_cards_off=plan_cards_off, with_cards=eff_with_cards):
                    continue
                if _cut_is_silent_in_env(float(sk["start"]), float(sk["end"]), seg_spoken_list[k], clip_env_cache.get(str(sk.get("src") or ""), [])):
                    continue
                if len(seg_words_list[k]) == 0:
                    inline_words = _seg_inline_words(sk)
                    if inline_words:
                        seg_spans, seg_sp_dur = _segment_tl_speech_spans(k, sk, seg_tl_bounds[k], seg_spoken_list[k])
                        if seg_sp_dur >= 0.18 and seg_spans:
                            synthesized = _synthesize_words_over_spans(inline_words, seg_spans)
                            seg_words_list[k] = synthesized.get(k, [])
            continue

        seg_spans_map: Dict[int, List[Tuple[int, float, float]]] = {}
        seg_sp_dur_map: Dict[int, float] = {}
        seg_weight_map: Dict[int, float] = {}
        for k in run_indices:
            sk = segs[k]
            tl_sp, sp_dur = _segment_tl_speech_spans(k, sk, seg_tl_bounds[k], seg_spoken_list[k])
            seg_spans_map[k] = tl_sp
            seg_sp_dur_map[k] = sp_dur
            fallback_w = max(0.04, _segment_play_duration(sk))
            seg_weight_map[k] = sp_dur if sp_dur >= 0.18 else fallback_w

        # Proportionally slice `expected` across multi-cut segments sharing this line ID
        n_run = len(run_indices)
        n_exp = len(expected)
        total_w = sum(seg_weight_map[k] for k in run_indices) or float(n_run)
        expected_by_seg: Dict[int, List[str]] = {}
        cum_w = 0.0
        w_start = 0
        for pos, k in enumerate(run_indices):
            cum_w += seg_weight_map[k]
            if pos == n_run - 1:
                w_end = n_exp
            else:
                rem = n_run - 1 - pos
                min_end = w_start + 1 if (n_exp - w_start) > rem else w_start
                max_end = (n_exp - rem) if n_exp >= n_run else n_exp
                target_cum = int(round(n_exp * (cum_w / total_w)))
                w_end = max(min_end, min(max_end, target_cum))
            expected_by_seg[k] = expected[w_start:w_end]
            w_start = w_end

        for k in run_indices:
            sk = segs[k]
            if _segment_subs_off(sk, plan_cards_off=plan_cards_off, with_cards=eff_with_cards):
                continue
            if _cut_is_silent_in_env(float(sk["start"]), float(sk["end"]), seg_spoken_list[k], clip_env_cache.get(str(sk.get("src") or ""), [])):
                continue
            seg_spans = seg_spans_map[k]
            seg_sp_dur = seg_sp_dur_map[k]
            if seg_sp_dur < 0.18 or not seg_spans:
                continue
            got_seg = len(seg_words_list[k])
            expected_seg = expected_by_seg[k] or _seg_inline_words(sk) or (expected[-1:] if got_seg == 0 else [])
            if not expected_seg:
                continue
            needs_synth = (got_seg == 0) or (len(expected_seg) >= 3 and got_seg < 0.65 * len(expected_seg))
            if needs_synth:
                synthesized = _synthesize_words_over_spans(expected_seg, seg_spans)
                seg_words_list[k] = synthesized.get(k, [])

    # Group words into rhythmic cues and bridge tiny intra-sentence gaps without overlapping
    cues: List[Dict[str, Any]] = []
    for i, words in enumerate(seg_words_list):
        if not words:
            continue
        t0, tl_end = seg_tl_bounds[i]
        spd_i = _segment_speed(segs[i])
        gap_scale = 1.0 / min(1.0, spd_i) if spd_i < 1.0 else 1.0
        eff_bridge_gap = BRIDGE_GAP_MAX * gap_scale
        words.sort(key=lambda x: (x["start"], x["end"]))
        groups = _group_segment_words(words, gap_scale=gap_scale)
        seg_cues = [_cue(g, max_end=tl_end) for g in groups if g]
        for k, c in enumerate(seg_cues):
            c["segIndex"] = i
            if k + 1 < len(seg_cues):
                next_st = seg_cues[k + 1]["startTime"]
                last_word_end = c["words"][-1]["endTime"]
                gap = next_st - last_word_end
                if 0.0 <= gap <= eff_bridge_gap and not SENTENCE_END.search(c["words"][-1]["text"]):
                    c["endTime"] = round(next_st, 3)
                    c["words"][-1]["endTime"] = round(next_st, 3)
                elif c["endTime"] > next_st:
                    c["endTime"] = round(next_st, 3)
                    for cw in c["words"]:
                        if cw["endTime"] > c["endTime"]:
                            cw["endTime"] = c["endTime"]
            else:
                if c["endTime"] > round(tl_end, 3):
                    c["endTime"] = round(tl_end, 3)
                    for cw in c["words"]:
                        if cw["endTime"] > c["endTime"]:
                            cw["endTime"] = c["endTime"]
        cues.extend(seg_cues)

    # Global non-overlap invariant across segment boundaries
    for k in range(len(cues) - 1):
        if cues[k]["endTime"] > cues[k + 1]["startTime"]:
            cues[k]["endTime"] = cues[k + 1]["startTime"]
            for cw in cues[k]["words"]:
                if cw["endTime"] > cues[k]["endTime"]:
                    cw["endTime"] = cues[k]["endTime"]

    return cues


# ---------------------------------------------------------------------------
# Layout-Aware Vertical Placement (`y=65` full-frame, `y=50` split_v seam)
# ---------------------------------------------------------------------------
def _is_split_v_segment(seg: Dict[str, Any]) -> bool:
    lay = str(seg.get("layout") or "single").split(":")[0].strip().lower()
    if lay in ("split_v", "gmeet_grid", "top_bottom", "vstack", "vertical_split", "split"):
        return True
    active = str(seg.get("active") or seg.get("slot") or "").strip().lower()
    if lay == "single" and active in ("top", "bottom"):
        return True
    return False


def _is_single_fullframe_segment(seg: Dict[str, Any]) -> bool:
    """Return True when `seg` uses a full-frame `single` layout (not a split, PiP, or baked split slot)."""
    if _is_split_v_segment(seg):
        return False
    lay = str(seg.get("layout") or "single").split(":")[0].strip().lower()
    if lay not in ("", "single", "full", "fullscreen"):
        return False
    active = str(seg.get("active") or seg.get("slot") or "").strip().lower()
    if active in ("top", "bottom", "left", "right"):
        return False
    return True


def _segment_face_boxes(
    seg: Dict[str, Any],
    width: int,
    height: int,
    cache: Dict[str, Optional[Dict[str, Any]]],
) -> List[Tuple[float, float, float, float]]:
    """Return normalized `(x0, y0, x1, y1)` detected face boxes in canvas coordinates for `seg`."""
    vis: Optional[Dict[str, Any]] = None
    if isinstance(seg.get("vision"), dict):
        vis = seg["vision"]
    elif isinstance(seg.get("faces"), list):
        vis = {"faces": seg["faces"]}
    elif isinstance(seg.get("bbox_norm"), (list, tuple)) and len(seg["bbox_norm"]) >= 4:
        vis = {"bbox_norm": seg["bbox_norm"]}
    else:
        src = seg.get("src") or seg.get("clip")
        if src:
            src_key = str(src)
            if src_key not in cache:
                try:
                    from common import resolve
                    from vision_tracker import analyze_clip_vision

                    cache[src_key] = analyze_clip_vision(resolve(src))
                except Exception:
                    cache[src_key] = None
            vis = cache.get(src_key)
    if not isinstance(vis, dict) or vis.get("baked_split"):
        return []

    raw_faces_field = vis.get("faces")
    faces: List[Any] = []
    if isinstance(raw_faces_field, list):
        max_conf = max(
            (
                float(f.get("confidence") or 0.0)
                for f in raw_faces_field
                if isinstance(f, dict) and "confidence" in f
            ),
            default=0.0,
        )
        for f in raw_faces_field:
            if isinstance(f, dict) and isinstance(f.get("bbox_norm"), (list, tuple)) and len(f["bbox_norm"]) >= 4:
                if "confidence" in f:
                    conf_v = float(f.get("confidence") or 0.0)
                    if conf_v <= 0.0 or (max_conf >= 0.85 and conf_v < 0.70):
                        continue
                faces.append(f["bbox_norm"])
            elif isinstance(f, (list, tuple)) and len(f) >= 4:
                faces.append(f)
    bb = vis.get("bbox_norm")
    if not faces and not raw_faces_field and isinstance(bb, (list, tuple)) and len(bb) >= 4:
        faces.append(bb)
    if not faces:
        return []

    foc = (
        seg["focus"]
        if isinstance(seg.get("focus"), (list, tuple)) and len(seg["focus"]) >= 2
        else (vis.get("focus") or [0.5, 0.38])
    )
    try:
        fx, fy = float(foc[0]), float(foc[1])
    except (TypeError, ValueError):
        fx, fy = 0.5, 0.38
    zmax = max(1.0, float(seg.get("zoom") or 1.0), float(seg.get("zoom_to") or 1.0))
    canvas_ar = float(width) / float(max(1, height))
    ar_raw = vis.get("aspect_ratio")
    ar = float(ar_raw) if ar_raw else canvas_ar

    out: List[Tuple[float, float, float, float]] = []
    for fb in faces:
        try:
            x0, y0, x1, y1 = float(fb[0]), float(fb[1]), float(fb[2]), float(fb[3])
        except (TypeError, ValueError):
            continue
        if ar > canvas_ar + 0.02:
            rx = ar / max(0.1, canvas_ar)
            x0 = x0 * rx - fx * (rx - 1.0)
            x1 = x1 * rx - fx * (rx - 1.0)
        elif ar < canvas_ar - 0.02:
            ry = canvas_ar / max(0.1, ar)
            y0 = y0 * ry - fy * (ry - 1.0)
            y1 = y1 * ry - fy * (ry - 1.0)
        zx0 = max(0.0, min(1.0, fx + (x0 - fx) * zmax))
        zx1 = max(0.0, min(1.0, fx + (x1 - fx) * zmax))
        zy0 = max(0.0, min(1.0, fy + (y0 - fy) * zmax))
        zy1 = max(0.0, min(1.0, fy + (y1 - fy) * zmax))
        if zx1 > zx0 + 0.01 and zy1 > zy0 + 0.01:
            out.append((zx0, zy0, zx1, zy1))
    return out


def _has_lower_mid_face(face_boxes: List[Tuple[float, float, float, float]]) -> bool:
    """Return True if any foreground face (`>= 12%` tall) overlaps the lower-mid caption band (`y in [0.61, 0.70]`)."""
    for fx0, fy0, fx1, fy1 in face_boxes:
        if fx1 > 0.10 and fx0 < 0.90 and (fy1 - fy0) >= 0.12 and fy1 > FACE_LOWER_MID_Y0 and fy0 < FACE_LOWER_MID_Y1:
            return True
    return False


def _caption_half_height_pct(width: int, height: int, spec: Optional[Dict[str, Any]] = None) -> float:
    """Height-aware caption half-height in % of canvas height (ensures clean 1:1 and 9:16 clearance)."""
    font_size = caption_font_size(width, spec)
    eff_w = float(width or 1080)
    eff_px = float(font_size) * (eff_w / 1080.0)
    return max(5.8, eff_px * 1.45 / float(max(1, height)) * 100.0)


def _load_flashcard_boxes(
    plan: Optional[Dict[str, Any]],
    width: int,
    height: int,
    report_dir: Optional[Path] = None,
    work_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Load active timeline flashcard boxes from `plan` or `overlays/flashcards[_sq]_meta.json`."""
    is_sq = (int(width) == int(height)) or (abs(int(width) - int(height)) <= max(1, int(width)) * 0.15)
    raw_cards: Any = None
    meta_w: Optional[float] = None
    meta_h: Optional[float] = None

    if isinstance(plan, dict):
        mem_key = "_flashcard_boxes_sq" if is_sq else "_flashcard_boxes"
        if plan.get(mem_key):
            raw_cards = plan.get(mem_key)

    if not raw_cards:
        fname = "flashcards_sq_meta.json" if is_sq else "flashcards_meta.json"
        cand_files: List[Path] = []
        if report_dir is not None:
            cand_files.append(Path(report_dir) / "overlays" / fname)
        if isinstance(plan, dict) and plan.get("_overlays_dir"):
            cand_files.append(Path(str(plan["_overlays_dir"])) / fname)
        if work_dir is not None:
            cand_files.append(Path(work_dir) / "overlays" / fname)
        for meta_f in cand_files:
            if meta_f.is_file():
                try:
                    data = json.loads(meta_f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw_cards = data.get("cards") or []
                        if data.get("canvas_w"):
                            meta_w = float(data["canvas_w"])
                        if data.get("canvas_h"):
                            meta_h = float(data["canvas_h"])
                    elif isinstance(data, list):
                        raw_cards = data
                    if raw_cards:
                        break
                except Exception:
                    continue

    if not isinstance(raw_cards, list):
        return []

    eff_w = float(meta_w or width or 1)
    eff_h = float(meta_h or height or 1)
    sx = float(width) / max(1.0, eff_w)
    sy = float(height) / max(1.0, eff_h)

    out: List[Dict[str, Any]] = []
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        try:
            t0 = float(item["start"] if item.get("start") is not None else item.get("t0", 0.0))
            t1 = float(item["end"] if item.get("end") is not None else item.get("t1", 0.0))
            y0_raw = float(item.get("y0", 0.0))
            if item.get("y1") is not None:
                y1_raw = float(item["y1"])
            elif item.get("h") is not None:
                y1_raw = y0_raw + float(item["h"])
            else:
                continue
            x0_raw = float(item.get("x0", 0.0))
            if item.get("x1") is not None:
                x1_raw = float(item["x1"])
            elif item.get("w") is not None:
                x1_raw = x0_raw + float(item["w"])
            else:
                x1_raw = eff_w
        except (TypeError, ValueError):
            continue
        if t1 <= t0 + 0.005 or y1_raw <= y0_raw:
            continue
        out.append({
            "start": t0,
            "end": t1,
            "t0": t0,
            "t1": t1,
            "x0": x0_raw * sx,
            "y0": y0_raw * sy,
            "x1": x1_raw * sx,
            "y1": y1_raw * sy,
        })
    return out


def _extract_raw_face_boxes(seg_or_vis: Dict[str, Any]) -> List[Tuple[float, float, float, float]]:
    """Extract normalized `(x0, y0, x1, y1)` face tuples directly from a segment or vision dict."""
    raw = seg_or_vis.get("faces")
    out: List[Tuple[float, float, float, float]] = []
    if isinstance(raw, list):
        for f in raw:
            bb = f.get("bbox_norm") if isinstance(f, dict) else f
            if isinstance(f, dict) and "confidence" in f and float(f.get("confidence") or 0.0) <= 0.0:
                continue
            if isinstance(bb, (list, tuple)) and len(bb) >= 4:
                try:
                    x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
                    if x1 > x0 + 0.01 and y1 > y0 + 0.01:
                        out.append((max(0.0, min(1.0, x0)), max(0.0, min(1.0, y0)), max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))))
                except (TypeError, ValueError):
                    continue
    return out


def _segment_dodge_face_boxes(
    seg: Dict[str, Any],
    width: int,
    height: int,
    cache: Dict[str, Optional[Dict[str, Any]]],
) -> List[Tuple[float, float, float, float]]:
    """Return normalized full-canvas face boxes `(x0, y0, x1, y1)` for subtitle collision dodging,
    supporting single-frame, baked-split, 1:1 square pillarbox, and `split_v` top/bottom panels."""
    is_sq = abs(int(width) - int(height)) <= max(1, int(width)) * 0.15
    eff_w = max(2, int(round(height * 9.0 / 16.0))) if is_sq else int(width)
    eff_h = int(height)

    def _map_sq_x(boxes: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
        if not is_sq:
            return boxes
        scale_x = 9.0 / 16.0
        return [
            (
                max(0.0, min(1.0, 0.5 + (fx0 - 0.5) * scale_x)),
                fy0,
                max(0.0, min(1.0, 0.5 + (fx1 - 0.5) * scale_x)),
                fy1,
            )
            for fx0, fy0, fx1, fy1 in boxes
        ]

    if _is_split_v_segment(seg):
        # If caller explicitly provided full-canvas faces spanning both panels, honor them directly.
        explicit_faces = _extract_raw_face_boxes(seg)
        if explicit_faces and any(b[1] < 0.48 for b in explicit_faces) and any(b[3] > 0.52 for b in explicit_faces):
            return _map_sq_x(explicit_faces)

        slot = str(seg.get("slot") or seg.get("active") or "").strip().lower()
        speaker = str(seg.get("speaker") or "").strip().lower()
        active_top = (
            slot in ("top", "slot_a", "a", "0")
            if slot
            else (("robot" in speaker) or ("nova" in speaker))
        )
        half_h = max(1, eff_h // 2)
        out: List[Tuple[float, float, float, float]] = []

        live_faces = _segment_face_boxes(seg, eff_w, half_h, cache)
        y_off_live = 0.0 if active_top else 0.5
        for fx0, fy0, fx1, fy1 in live_faces:
            out.append((fx0, y_off_live + fy0 * 0.5, fx1, y_off_live + fy1 * 0.5))

        peer = seg.get("peer")
        peer_seg: Optional[Dict[str, Any]] = None
        if isinstance(peer, dict) and (peer.get("src") or peer.get("clip") or peer.get("faces") or peer.get("vision")):
            peer_seg = peer
        elif isinstance(peer, str) and peer.strip():
            p_ref = peer.split("@")[0].strip()
            if ":" in p_ref and len(p_ref.split(":", 1)[0]) > 1:
                p_ref = p_ref.split(":", 1)[1].strip()
            if p_ref:
                peer_seg = {"src": p_ref}
        if peer_seg is not None:
            peer_faces = _segment_face_boxes(peer_seg, eff_w, half_h, cache)
            y_off_peer = 0.5 if active_top else 0.0
            for fx0, fy0, fx1, fy1 in peer_faces:
                out.append((fx0, y_off_peer + fy0 * 0.5, fx1, y_off_peer + fy1 * 0.5))

        return _map_sq_x(out)

    faces = _segment_face_boxes(seg, eff_w, eff_h, cache)
    if not faces:
        vis = seg.get("vision") if isinstance(seg.get("vision"), dict) else None
        if vis is None and (seg.get("src") or seg.get("clip")):
            vis = cache.get(str(seg.get("src") or seg.get("clip")))
        if isinstance(vis, dict) and vis.get("baked_split"):
            faces = _extract_raw_face_boxes(vis)
    return _map_sq_x(faces)


def _interval_overlap_pct(
    cand_y: float,
    intervals: List[Tuple[float, float]],
    half_h: float,
    margin: float,
) -> float:
    """Return total vertical overlap (in % of canvas height) between `[cand_y - half_h, cand_y + half_h]`
    and a list of per-box `(y0_pct, y1_pct)` intervals expanded by `margin`."""
    c0 = cand_y - half_h
    c1 = cand_y + half_h
    ov_sum = 0.0
    for y0, y1 in intervals:
        ov = min(c1, y1 + margin) - max(c0, y0 - margin)
        if ov > 0.0:
            ov_sum += ov
    return ov_sum


def _pick_non_overlapping_y(
    default_cue_y: float,
    card_intervals: List[Tuple[float, float]],
    banner_intervals: List[Tuple[float, float]],
    face_intervals: List[Tuple[float, float]],
    cap_half_pct: float,
    pad_pct: float,
    is_square: bool,
) -> float:
    """Pick the cleanest Y candidate (%) avoiding active flashcards, banners, and detected face boxes."""
    min_y = max(16.0, cap_half_pct + 2.0)
    max_y = min(90.0, 100.0 - cap_half_pct - 2.0)
    if max_y < min_y:
        min_y, max_y = 18.0, 88.0

    raw_cands: List[float] = [default_cue_y]
    for by0, by1 in card_intervals + banner_intervals:
        raw_cands.append(by1 + cap_half_pct + pad_pct + 0.05)
        raw_cands.append(by0 - cap_half_pct - pad_pct - 0.05)
    for fy0, fy1 in face_intervals:
        raw_cands.append(fy1 + cap_half_pct + 1.55)
        raw_cands.append(fy0 - cap_half_pct - 1.55)
    raw_cands.extend([65.0, 79.0, 50.0, 83.0, 38.0, 86.0, 56.0, 24.0])
    if is_square:
        raw_cands.append(82.0)

    cands: List[float] = []
    seen: Set[float] = set()
    for cy in raw_cands:
        clamped = round(max(min_y, min(max_y, float(cy))), 2)
        if clamped not in seen:
            seen.add(clamped)
            cands.append(clamped)

    def _score(cy: float) -> Tuple[int, int, int, float, float]:
        ov_card = _interval_overlap_pct(cy, card_intervals, cap_half_pct, pad_pct)
        ov_banner = _interval_overlap_pct(cy, banner_intervals, cap_half_pct, pad_pct)
        ov_face = _interval_overlap_pct(cy, face_intervals, cap_half_pct, 1.0)
        return (
            1 if ov_card > 0.01 else 0,
            1 if ov_face > 0.01 else 0,
            1 if ov_banner > 0.01 else 0,
            round(ov_card * 2.0 + ov_face * 2.0 + ov_banner, 3),
            round(abs(cy - default_cue_y), 3),
        )

    return min(cands, key=_score)


def resolve_safe_y_percent(
    spec: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    width: int,
    height: int,
    cues: Optional[List[Dict[str, Any]]] = None,
    flashcard_boxes: Optional[List[Dict[str, Any]]] = None,
    plan: Optional[Dict[str, Any]] = None,
    report_dir: Optional[Path] = None,
) -> float:
    """Compute default `y_percent` (`65.0` on `9:16`, `82.0` on `1:1` square, `50.0` on `split_v` seam)
    and attach per-cue `style.positionY` overrides when cuts alternate between `split_v` and `single`,
    show banners or active flashcards, or contain a lower-mid foreground face overlapping the default `~65%` band."""
    raw_y = spec.get("y")
    explicit_y = raw_y is not None and str(raw_y).strip().lower() not in ("", "auto", "none")
    is_square = abs(width - height) <= width * 0.15
    base_single_y = float(raw_y) if explicit_y else (82.0 if is_square else SINGLE_Y)
    try:
        seam_y = float(spec.get("split_y")) if spec.get("split_y") is not None else SEAM_Y
    except (TypeError, ValueError):
        seam_y = SEAM_Y

    split_v_dur = 0.0
    measured_dur = 0.0
    for i, s in enumerate(segs):
        t0 = float(starts[i]) if i < len(starts) else 0.0
        t1 = float(starts[i + 1]) if i + 1 < len(starts) else max(t0, float(total))
        d = max(0.0, t1 - t0)
        measured_dur += d
        if _is_split_v_segment(s):
            split_v_dur += d

    majority_split_v = measured_dur > 0.0 and (split_v_dur / measured_dur) >= 0.50
    default_y = seam_y if majority_split_v else base_single_y

    if not cues:
        return default_y

    if flashcard_boxes is None:
        flashcard_boxes = _load_flashcard_boxes(plan, width, height, report_dir=report_dir)

    cap_half_pct = _caption_half_height_pct(width, height, spec)
    pad_pct = max(1.5, 22.0 / float(max(1, height)) * 100.0)

    banner_fn = None
    try:
        from layouts import banner_boxes as banner_fn
    except Exception:
        banner_fn = None

    face_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    for c in cues:
        seg_idx = c.get("segIndex")
        if seg_idx is None:
            mid_t = 0.5 * (float(c["startTime"]) + float(c["endTime"]))
            seg_idx = 0
            for idx in range(len(segs)):
                t0 = float(starts[idx]) if idx < len(starts) else 0.0
                t1 = float(starts[idx + 1]) if idx + 1 < len(starts) else float(total)
                if t0 - 0.05 <= mid_t <= t1 + 0.05:
                    seg_idx = idx
                    break
        if not (0 <= seg_idx < len(segs)):
            continue

        seg = segs[seg_idx]
        t0 = float(starts[seg_idx]) if seg_idx < len(starts) else 0.0
        cue_y = seam_y if _is_split_v_segment(seg) else base_single_y

        # Build per-box forbidden Y intervals for banners (do NOT merge distant boxes!)
        banner_intervals: List[Tuple[float, float]] = []
        if banner_fn is not None and (seg.get("banners") or seg.get("stop_saying")):
            try:
                seg_dur = max(0.08, segment_duration(seg))
                boxes = banner_fn(seg, width, height, seg_dur)
                rel_a = max(0.0, float(c["startTime"]) - t0)
                rel_b = max(rel_a + 0.04, float(c["endTime"]) - t0)
                active_banners = [b for b in boxes if float(b["t1"]) >= rel_a and float(b["t0"]) <= rel_b]
                if active_banners and height > 0:
                    for b in active_banners:
                        by0 = float(b["y0"]) / float(height) * 100.0
                        by1 = float(b["y1"]) / float(height) * 100.0
                        if by1 > by0:
                            banner_intervals.append((by0, by1))
            except Exception:
                pass

        # Build per-box forbidden Y intervals for active timeline flashcards overlapping this cue
        card_intervals: List[Tuple[float, float]] = []
        if flashcard_boxes and height > 0:
            c_st = float(c["startTime"])
            c_en = float(c["endTime"])
            for fc in flashcard_boxes:
                if not isinstance(fc, dict):
                    continue
                try:
                    f_st = float(fc["start"] if fc.get("start") is not None else fc.get("t0", 0.0))
                    f_en = float(fc["end"] if fc.get("end") is not None else fc.get("t1", 0.0))
                    fy0_px = float(fc.get("y0", 0.0))
                    fy1_px = float(fc["y1"]) if fc.get("y1") is not None else (fy0_px + float(fc.get("h", 0.0)))
                except (TypeError, ValueError):
                    continue
                if min(c_en, f_en) - max(c_st, f_st) > 0.005 and fy1_px > fy0_px:
                    card_intervals.append((fy0_px / float(height) * 100.0, fy1_px / float(height) * 100.0))

        blocked_intervals = banner_intervals + card_intervals
        overlay_dodged = False

        if blocked_intervals and _interval_overlap_pct(cue_y, blocked_intervals, cap_half_pct, pad_pct) > 0.0:
            dodge_faces = _segment_dodge_face_boxes(seg, width, height, face_cache)
            face_intervals = [
                (fy0 * 100.0, fy1 * 100.0)
                for fx0, fy0, fx1, fy1 in dodge_faces
                if fx1 > 0.10 and fx0 < 0.90 and fy1 > fy0
            ]
            cue_y = _pick_non_overlapping_y(
                cue_y,
                card_intervals,
                banner_intervals,
                face_intervals,
                cap_half_pct,
                pad_pct,
                is_square,
            )
            overlay_dodged = True

        # V1-approved smart face-avoidance drop: shift single-frame ~65% captions down to 79.0%
        # when a foreground face (>=12% tall) occupies the lower-mid zone [0.61, 0.70] and no banner/flashcard sits at 79.0%.
        if (
            not overlay_dodged
            and not is_square
            and _is_single_fullframe_segment(seg)
            and (not explicit_y or abs(cue_y - SINGLE_Y) <= 5.0)
            and 55.0 <= cue_y <= 73.0
        ):
            face_boxes = _segment_face_boxes(seg, width, height, face_cache)
            if _has_lower_mid_face(face_boxes):
                drop_blocked = _interval_overlap_pct(FACE_DROP_Y, blocked_intervals, cap_half_pct, pad_pct) > 0.0
                if not drop_blocked:
                    cue_y = FACE_DROP_Y
                elif card_intervals:
                    face_intervals = [
                        (fy0 * 100.0, fy1 * 100.0)
                        for fx0, fy0, fx1, fy1 in face_boxes
                        if fx1 > 0.10 and fx0 < 0.90 and fy1 > fy0
                    ]
                    cue_y = _pick_non_overlapping_y(
                        cue_y,
                        card_intervals,
                        banner_intervals,
                        face_intervals,
                        cap_half_pct,
                        pad_pct,
                        is_square,
                    )

        if abs(cue_y - default_y) >= 0.5:
            c.setdefault("style", {})["positionY"] = round(cue_y, 2)

    return default_y


# ---------------------------------------------------------------------------
# Node Caption Rendering, FFmpeg Concat Demuxer & SRT Export
# ---------------------------------------------------------------------------
def render_frames(
    cues: List[Dict[str, Any]],
    style: Dict[str, Any],
    width: int,
    height: int,
    out_dir: Path,
    y_percent: float = 65.0,
    fps: int = 25,
    return_meta: bool = False,
    font_size: Optional[int] = None,
    font_family: Optional[str] = None,
) -> Any:
    """Render every caption state to cropped transparent PNGs via `subtitles/render_cues.js`.
    Returns the `frames` list by default, or the full manifest dict when `return_meta=True`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_cues = [{k: v for k, v in c.items() if k != "segIndex"} for c in cues]
    job = {
        "width": width,
        "height": height,
        "fps": max(24, int(fps or 25)),
        "styleId": style["id"],
        "outDir": str(out_dir),
        "positionY": y_percent,
        "fontSize": int(font_size or caption_font_size(width)),
        "singleLine": True,
        "maxLines": 1,
        "cues": clean_cues,
    }
    if font_family and str(font_family).strip():
        job["fontFamily"] = resolve_font(font_family) or str(font_family).strip()
    jf = out_dir / "job.json"
    jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [node_path(), str(SUB_DIR / "render_cues.js"), str(jf)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
    )
    if r.returncode != 0:
        raise RuntimeError(f"subtitle renderer failed: {(r.stderr or r.stdout).strip()[-400:]}")
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1]
    manifest = json.loads(line)
    return manifest if return_meta else manifest["frames"]


def write_concat_list(
    frames: List[Dict[str, Any]],
    total: float,
    width: int,
    height: int,
    out_dir: Path,
    fps: int = 25,
) -> Path:
    """Write an FFmpeg `ffconcat` file spanning `0..total` seconds, filling non-spoken intervals with transparent `blank.png`."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    blank_w, blank_h = int(width), int(height)
    for f in frames:
        fp = Path(str(f.get("path") or ""))
        if fp.is_file():
            try:
                with Image.open(fp) as im:
                    blank_w, blank_h = im.size
                break
            except Exception:
                continue

    blank = out_dir / "blank.png"
    Image.new("RGBA", (max(2, blank_w), max(2, blank_h)), (0, 0, 0, 0)).save(blank, compress_level=1)

    min_dur = 1.0 / max(24, int(fps or 25))
    ordered = sorted(
        frames,
        key=lambda x: (
            float(x["startSec"]) if "startSec" in x else float(x["startMs"]) / 1000.0,
            int(x.get("captionIndex", 0)),
            int(x.get("stepIndex", 0)),
            int(x.get("subIndex", 0)),
        ),
    )

    lines = ["ffconcat version 1.0"]
    t = 0.0
    last = str(blank)
    for f in ordered:
        raw_a = float(f["startSec"]) if "startSec" in f else float(f["startMs"]) / 1000.0
        raw_b = float(f["endSec"]) if "endSec" in f else (float(f["startMs"]) + float(f["durationMs"])) / 1000.0
        a = max(t, raw_a)
        if a >= total - 0.001:
            break
        # Bridge <15ms subframe rounding micro-gaps so captions never blink transparent mid-cue
        if t > 0.0 and 0.0 < (a - t) < 0.015:
            a = t
        elif a - t > 0.001:
            lines += [f"file '{blank.as_posix()}'", f"duration {a - t:.4f}"]
        b = min(total, max(a + min(min_dur, max(0.02, total - a)), raw_b))
        if b - a <= 0.0005:
            continue
        lines += [f"file '{Path(f['path']).as_posix()}'", f"duration {b - a:.4f}"]
        last = f["path"]
        t = b
    if total - t > 0.001:
        lines += [f"file '{blank.as_posix()}'", f"duration {total - t:.4f}"]
        last = str(blank)
    lines.append(f"file '{Path(last).as_posix()}'")
    lst = out_dir / "subs.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def write_srt(cues: List[Dict[str, Any]], path: Path) -> None:
    """Write standard SubRip (`.srt`) sidecar subtitles from timeline `cues`."""
    def ts(x: float) -> str:
        ms = int(round(max(0.0, x) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{ts(c['startTime'])} --> {ts(c['endTime'])}", c["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------------------
# Cross-Job Style Rotation (`#subs auto`)
# ---------------------------------------------------------------------------
HISTORY = "_subs_history.json"


def _history_file(work_dir: Path) -> Path:
    return Path(work_dir) / HISTORY


def recent_styles(work_dir: Optional[Path] = None, n: int = 3) -> List[str]:
    """Return the last `n` subtitle style names used on this machine, newest first."""
    from common import WORK

    try:
        d = json.loads(_history_file(work_dir or WORK).read_text(encoding="utf-8"))
        return [str(x.get("style")) for x in reversed(d.get("used", []))][:n]
    except Exception:  # noqa: BLE001
        return []


def remember_style(name: str, project: str, work_dir: Optional[Path] = None) -> None:
    """Record `name` as the subtitle style used for `project` without double-counting re-renders."""
    from common import WORK

    f = _history_file(work_dir or WORK)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    used = [x for x in d.get("used", []) if x.get("project") != project]
    used.append({"style": name, "project": project, "at": time.strftime("%Y-%m-%d %H:%M")})
    try:
        f.write_text(json.dumps({"used": used[-20:]}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def choose_style(project: str, work_dir: Optional[Path] = None, avoid: int = 2) -> Dict[str, Any]:
    """Select a deterministic style for `#subs auto` weighted by `autoWeight` and avoiding the last `avoid` styles."""
    pool = [x for x in styles() for _ in range(max(1, int(x.get("autoWeight", 6))))]
    recent = set(recent_styles(work_dir, avoid))
    fresh = [s for s in pool if s["name"] not in recent] or pool
    h = sum(ord(c) * (k + 1) for k, c in enumerate(project or "zeno"))
    return fresh[h % len(fresh)]


# ---------------------------------------------------------------------------
# Scene-Aware Contrast & Hue Adaptation (`adapt=on`)
# ---------------------------------------------------------------------------
SAFE_FILLS = {
    "bright": ["#FACC15", "#22D3EE", "#A3E635", "#F472B6", "#FB923C", "#60A5FA", "#C084FC", "#FFFFFF"],
    "box": ["#2563EB", "#7C3AED", "#15803D", "#DC2626", "#DB2777", "#0F766E", "#C2410C"],
}


def _rgb(hexcol: str) -> Tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _lum(rgb: Tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _hue(hexcol: str) -> float:
    return colorsys.rgb_to_hsv(*_rgb(hexcol))[0] * 360.0


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def band_stats(img: Any, y_percent: float) -> Dict[str, float]:
    """Measure mean luminance, dominant hue, saturation ratio, and dark-pixel ratio on the horizontal band around `y_percent`."""
    import numpy as np

    w, h = img.size
    y0, y1 = int(h * max(0.0, y_percent - 7.0) / 100.0), int(h * min(100.0, y_percent + 7.0) / 100.0)
    x0, x1 = int(w * 0.08), int(w * 0.92)
    a = np.asarray(img.convert("RGB").crop((x0, y0, x1, max(y1, y0 + 1))), dtype="float32") / 255.0
    px = a.reshape(-1, 3)
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0.0)
    lum = 0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]
    colourful = sat > 0.25
    hue = 0.0
    if colourful.sum() > 0.04 * len(px):
        hsv = np.array([colorsys.rgb_to_hsv(*c) for c in px[colourful][:: max(1, int(colourful.sum() // 400))]])
        ang = hsv[:, 0] * 2 * np.pi
        weights = hsv[:, 1] * hsv[:, 2]
        hue = float(np.degrees(np.arctan2((np.sin(ang) * weights).sum(), (np.cos(ang) * weights).sum())) % 360.0)
    return {
        "lum": float(_lum(tuple(px.mean(axis=0)))),
        "hue": float(hue),
        "colour": float(colourful.mean()),
        "dark": float((lum < 0.25).mean()),
    }


def adapt_style(style: Dict[str, Any], st: Dict[str, float]) -> Dict[str, Any]:
    """Return per-cue color overrides that tint dark bars toward the scene hue while picking high-contrast active word accents."""
    kind_mode = style.get("highlightMode") or ""
    sat = st["colour"]
    bg_hue = st["hue"]

    def best(cands: List[str], vs_dark: bool = True) -> str:
        weight = max(0.25, min(1.0, sat * 2.0))

        def score(c: str) -> float:
            gap = _hue_gap(_hue(c), bg_hue) / 180.0 if sat > 0.12 else 0.5
            ctr = min(_contrast(_lum(_rgb(c)), 0.02 if vs_dark else st["lum"]), 9.0) / 9.0
            return gap * weight + ctr * 0.6

        return max(cands, key=score)

    out: Dict[str, Any] = {}

    def dark_tint(alpha: float) -> str:
        r, g, b = colorsys.hsv_to_rgb((bg_hue % 360.0) / 360.0, min(0.55, 0.15 + sat * 0.6), 0.14)
        return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, {alpha})"

    if kind_mode == "docu-bar":
        out["bgColor"] = dark_tint(0.90)
        out["activeColor"] = best(SAFE_FILLS["bright"])
    elif kind_mode == "frosted-lyric":
        out["bgColor"] = dark_tint(0.82)
        out["activeColor"] = best(["#38BDF8", "#22D3EE", "#FACC15", "#A3E635", "#F472B6", "#C084FC"])
    elif style.get("isGlossy") or int(style.get("id", -1)) == 3:
        out["activeColor"] = best(SAFE_FILLS["bright"])
    elif kind_mode == "word-box":
        out["wordBoxColor"] = best(SAFE_FILLS["box"], vs_dark=False)
    elif kind_mode == "gradient-badge":
        c = best(SAFE_FILLS["box"], vs_dark=False)
        out["wordBoxColor"] = c
        out["activeColor"] = c
    elif kind_mode == "cinema":
        base_c = best(["#FFE600", "#FFFFFF", "#00E5FF", "#A3E635"], vs_dark=False)
        out["color"] = base_c
        out["activeColor"] = "#FFE600" if base_c.upper() == "#FFFFFF" else "#FFFFFF"
    elif kind_mode == "comic-slap":
        out["activeColor"] = best(["#FACC15", "#22D3EE", "#A3E635", "#F472B6"], vs_dark=False)
    return out


def _frames_for_cues(video: Path, cues: List[Dict[str, Any]], out_dir: Path) -> List[Tuple[float, Any]]:
    """Sample small RGB frames from `video` at 4 fps for background color analysis."""
    from PIL import Image
    from common import ffmpeg

    d = out_dir / "probe"
    d.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(video), "-vf", "fps=4,scale=108:192", "-f", "image2", str(d / "f%05d.png"), timeout=600)
    files = sorted(d.glob("f*.png"))
    return [(i / 4.0, Image.open(f).convert("RGB")) for i, f in enumerate(files)]


def build_overlay(
    plan: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    width: int,
    height: int,
    work_dir: Path,
    srt_path: Optional[Path] = None,
    video: Optional[Path] = None,
    report_dir: Optional[Path] = None,
    fps: int = 25,
    subdir: str = "_subs",
) -> Optional[Path]:
    """Build the subtitle overlay concat file (`subs.ffconcat`) and metadata (`subs_meta.json`) inside `(report_dir or work_dir) / subdir`."""
    spec = plan.get("subs")
    if not isinstance(spec, dict) or not spec.get("style"):
        return None
    if not ensure_ready():
        print("  [SUBS] Node.js or node-canvas is unavailable. Rendering without subtitles.")
        return None

    project = Path(str(plan.get("output") or "zeno")).stem
    if str(spec["style"]).strip().lower() == "auto":
        style = choose_style(project, work_dir)
    else:
        style = resolve_style(spec["style"])
    if style is None:
        style = choose_style(project, work_dir)
        style_list = ", ".join(f"{s['id']} {s['name']}" for s in styles())
        print(f"  [SUBS] Unknown style '{spec['style']}' (available: auto, {style_list}). Falling back to '{style['name']}'.")

    script_lines: List[Dict[str, Any]] = []
    try:
        from prepare import parse_script_file
        from common import resolve

        if plan.get("script"):
            _, script_lines = parse_script_file(resolve(plan["script"]))
    except Exception:  # noqa: BLE001
        script_lines = []

    cache_hint = Path(report_dir) if (report_dir is not None and (Path(report_dir) / "clip_caches.json").is_file()) else Path(work_dir)
    eff_font_size = caption_font_size(width, spec)
    size_scale = 54.0 / max(54.0, float(eff_font_size))
    hl_mode = str(style.get("highlightMode") or "").strip().lower()
    bg_col = str(style.get("bgColor") or style.get("bg") or "").strip().lower()
    has_bg = bool(bg_col and bg_col not in ("transparent", "none"))
    if hl_mode == "sticker" or style.get("id") == 12:
        pad_scale = 0.80
    elif (
        hl_mode in ("docu-bar", "frosted-lyric", "wireframe-glass", "word-box")
        or style.get("id") in (3, 4, 17, 25, 28)
        or bool(style.get("isGlossy"))
        or has_bg
    ):
        pad_scale = 0.86
    else:
        pad_scale = 1.0
    global MAX_VISUAL_UNITS
    _units_before = MAX_VISUAL_UNITS
    MAX_VISUAL_UNITS = _units_before * size_scale * pad_scale
    eff_with_cards = _flag_truthy(spec.get("with_cards"))
    try:
        cues = build_cues(
            segs,
            starts,
            script_lines,
            work_dir=cache_hint,
            plan=plan,
            with_cards=eff_with_cards,
        )
    finally:
        MAX_VISUAL_UNITS = _units_before
    if not cues:
        plan_cards_off = _plan_cards_off(plan)
        suppressed_all = any(
            not (_segment_is_beat(s) or _segment_is_muted(s))
            and _segment_subs_off(s, plan_cards_off=plan_cards_off, with_cards=eff_with_cards)
            for s in segs
        )
        if suppressed_all:
            print("  [SUBS] All spoken segments suppressed by flashcards or subs=off; skipping bottom subtitles.")
        else:
            print("  [SUBS] No spoken words found for subtitles.")
        return None

    base_out = Path(report_dir) if report_dir is not None else Path(work_dir)
    out_dir = base_out / subdir
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    eff_fps = max(24, int(fps or (plan.get("canvas") or {}).get("fps") or 25))
    fc_boxes = _load_flashcard_boxes(plan, width, height, report_dir=report_dir, work_dir=work_dir)
    y_pct = resolve_safe_y_percent(
        spec,
        segs,
        starts,
        total,
        width,
        height,
        cues=cues,
        flashcard_boxes=fc_boxes,
        plan=plan,
        report_dir=report_dir,
    )

    adapted = 0
    if video is not None and spec.get("adapt", False):
        try:
            probes = _frames_for_cues(Path(video), cues, out_dir)
            if probes:
                for c in cues:
                    c_y = float((c.get("style") or {}).get("positionY", y_pct))
                    near = [im for t, im in probes if c["startTime"] - 0.15 <= t <= c["endTime"] + 0.15] or [
                        min(probes, key=lambda p: abs(p[0] - c["startTime"]))[1]
                    ]
                    stats = [band_stats(im, c_y) for im in near]
                    avg = {k: sum(s[k] for s in stats) / len(stats) for k in stats[0]}
                    avg["hue"] = stats[len(stats) // 2]["hue"]
                    ov = adapt_style(style, avg)
                    if ov:
                        c["style"] = {**(c.get("style") or {}), **ov}
                        adapted += 1
            shutil.rmtree(out_dir / "probe", ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [SUBS] Could not sample background colors, keeping default style palette: {exc}")

    custom_font: Optional[str] = None
    if spec.get("font"):
        custom_font = resolve_font(spec["font"])
        if custom_font is None:
            print(f"  [SUBS] Unknown font '{spec['font']}'; keeping style default '{style.get('fontFamily', 'Montserrat')}'.")

    manifest = render_frames(
        cues,
        style,
        width,
        height,
        out_dir,
        y_pct,
        fps=eff_fps,
        return_meta=True,
        font_size=eff_font_size,
        font_family=custom_font,
    )
    frames = manifest["frames"]
    band_y0 = int(manifest.get("bandY0", 0))
    band_h = int(manifest.get("bandHeight", height))

    if srt_path is not None:
        write_srt(cues, srt_path)
    remember_style(style["name"], project, work_dir)
    if report_dir is not None:
        try:
            import job_report

            job_report.record_subs(report_dir, style["name"], len(cues), adapted)
        except Exception:  # noqa: BLE001
            pass

    ffconcat_path = write_concat_list(frames, total, width, band_h, out_dir, fps=eff_fps)

    cue_half_px = (_caption_half_height_pct(width, height, spec) / 100.0) * float(height)
    meta_cues = []
    for ci, c in enumerate(cues):
        eff_y = float((c.get("style") or {}).get("positionY", y_pct))
        cy_px = (eff_y / 100.0) * float(height)
        y0_px = max(0, int(round(cy_px - cue_half_px)))
        y1_px = min(int(height), int(round(cy_px + cue_half_px)))
        meta_cues.append({
            "index": ci,
            "start": round(float(c["startTime"]), 4),
            "end": round(float(c["endTime"]), 4),
            "text": str(c.get("text") or ""),
            "y_percent": round(eff_y, 2),
            "y0_px": y0_px,
            "y1_px": y1_px,
        })

    default_cy_px = (float(y_pct) / 100.0) * float(height)
    default_y0_px = max(0, int(round(default_cy_px - cue_half_px)))
    default_y1_px = min(int(height), int(round(default_cy_px + cue_half_px)))

    meta_frames = []
    for f in frames:
        st_s = float(f["startSec"]) if "startSec" in f else float(f["startMs"]) / 1000.0
        en_s = float(f["endSec"]) if "endSec" in f else (float(f["startMs"]) + float(f["durationMs"])) / 1000.0
        cue_idx = int(f.get("captionIndex", 0))
        cue_geo = meta_cues[cue_idx] if 0 <= cue_idx < len(meta_cues) else None
        meta_frames.append({
            "file": str(Path(f["path"]).resolve()),
            "start": round(st_s, 4),
            "end": round(en_s, 4),
            "text": str(f.get("text") or ""),
            "cue": cue_idx,
            "word": int(f.get("stepIndex", 0)),
            "sub": int(f.get("subIndex", 0)),
            "y_percent": cue_geo["y_percent"] if cue_geo else round(y_pct, 2),
            "y0_px": cue_geo["y0_px"] if cue_geo else default_y0_px,
            "y1_px": cue_geo["y1_px"] if cue_geo else default_y1_px,
        })

    subs_meta = {
        "band_y0": band_y0,
        "band_h": band_h,
        "width": int(width),
        "height": int(height),
        "fps": eff_fps,
        "y_percent": round(y_pct, 2),
        "style": style["name"],
        "style_id": style["id"],
        "font_family": manifest.get("fontFamily") or custom_font or style.get("fontFamily"),
        "cues_count": len(cues),
        "cues": meta_cues,
        "frames": meta_frames,
        "srt_path": str(srt_path) if srt_path else None,
    }
    (out_dir / "subs_meta.json").write_text(json.dumps(subs_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    font_note = f", font '{subs_meta['font_family']}'" if custom_font else ""
    print(
        f"  [SUBS] {len(cues)} captions, {len(frames)} frames "
        f"(band y={band_y0}..{band_y0 + band_h}), style '{style['name']}'{font_note}, "
        f"colors adapted on {adapted} captions"
    )
    try:
        import events

        events.emit(
            "subs",
            f"Subtitles: {len(cues)} captions in '{style['name']}'{font_note}, colors adapted on {adapted}",
            style=style["name"],
            captions=len(cues),
        )
    except Exception:
        pass
    return ffconcat_path


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) > 1 and _sys.argv[1] == "--ensure":
        ok = ensure_ready()
        print("  [SUBS] captions ready" if ok else "  [SUBS] captions NOT ready (see above)")
        _sys.exit(0 if ok else 1)
    if len(_sys.argv) > 1 and _sys.argv[1] == "--list-fonts":
        script_arg = _sys.argv[2] if len(_sys.argv) > 2 else None
        items = list_fonts(script=script_arg)
        for rec in items:
            ws = ",".join(str(w) for w in rec["weights"])
            sc = "/".join(rec["scripts"])
            print(f"{rec['family']} [{ws}] ({sc})")
        print(f"Total: {len(items)} bundled static Google Font families")
        _sys.exit(0)
