"""Burn-in subtitles for a rendered edit, only when the plan asks for them (`#subs <style> [y=70]` in plan.edl).

The words come from what the clips actually say (the aligned transcript, with each word's time), shifted onto the finished timeline.
They are never translated: Kannada stays Kannada, English stays English. The pictures are drawn by Edit Flow AI's caption renderer
(`subtitles/caption-renderer.js`, ten styles in `subtitles/styles.json`) running on node-canvas, one transparent PNG per spoken word state.
render.py overlays them in the final mix as one image sequence. Standard library only, plus Node for the drawing.
"""
from __future__ import annotations

import colorsys
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SUB_DIR = HERE.parent / "subtitles"
MAX_WORDS_PER_CUE = 4          # 2 to 4 words on screen at a time reads fast enough for a Meta ad
MAX_GAP_IN_CUE = 0.60          # a longer silence between two words starts a new caption
SENTENCE_END = re.compile(r"[.?!।]$")


def node_path() -> str:
    return shutil.which("node") or ""


def available() -> bool:
    """Node is installed and the renderer's dependencies (node-canvas) are in place."""
    return bool(node_path()) and (SUB_DIR / "node_modules" / "canvas").is_dir() and (SUB_DIR / "styles.json").is_file()


def ensure_ready() -> bool:
    """Install the renderer's one dependency (node-canvas, a prebuilt binary of about 20 MB) the first time subtitles are used on this computer.
    Needs Node and an internet connection; returns whether subtitles can be drawn."""
    if available():
        return True
    npm = shutil.which("npm")
    if node_path() and npm and (SUB_DIR / "package.json").is_file():
        print("  [SUBS] first use on this computer: installing the caption renderer (about 20 MB)...")
        try:
            subprocess.run([npm, "install", "--no-audit", "--no-fund", "--silent"], cwd=str(SUB_DIR), capture_output=True, text=True, timeout=600)
        except Exception as exc:   # noqa: BLE001
            print(f"  [SUBS] install failed: {exc}")
    return available()


def styles() -> List[Dict[str, Any]]:
    return json.loads((SUB_DIR / "styles.json").read_text(encoding="utf-8"))


def resolve_style(token: Any) -> Optional[Dict[str, Any]]:
    """A style by id (`9`) or by name (`Hormozi Punch`, any case); None when it is not one of the ten."""
    tok = str(token).strip().lower()
    for s in styles():
        if str(s.get("id")) == tok or str(s.get("name", "")).lower() == tok:
            return s
    return None


def _words_of_clip(src: Any) -> List[Dict[str, Any]]:
    from common import clip_work_dir

    wd = clip_work_dir(src)
    for fname in ("hq_words.json", "words.json"):
        f = wd / fname
        if f.is_file():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                d = d.get("words", d) if isinstance(d, dict) else d
                if d:
                    return [w for w in d if isinstance(w, dict)]
            except Exception:
                continue
    return []


def _speech_spans(src: Any) -> List[Tuple[float, float]]:
    from common import clip_work_dir

    f = clip_work_dir(src) / "speech_spans.json"
    try:
        return [tuple(x) for x in json.loads(f.read_text(encoding="utf-8")).get("spans", [])]
    except Exception:
        return []


def _base_line(seg_id: str) -> str:
    m = re.match(r"^(L\d+)", str(seg_id))
    return m.group(1) if m else str(seg_id)


def _foreign_to_line(word: str, seg_line: str, line_tokens: Dict[str, List[str]], similarity) -> bool:
    """A transcribed word that matches another script line better than the cut's own line is a stray word of a neighbouring line
    (the transcriber sometimes writes the next line's words into a short clip): leave it off the screen."""
    from script_order import clean

    toks = clean([word])
    if not toks:
        return False
    tok = toks[0]
    hits = [lid for lid, lt in line_tokens.items() if any(similarity(tok, x) >= 0.8 for x in lt)]
    return bool(hits) and seg_line not in hits


def build_cues(segs: List[Dict[str, Any]], starts: List[float], script_lines: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Caption cues on the finished timeline (seconds): 2 to 4 words each, every word with its start time."""
    try:
        from prepare import _word_similarity as similarity
    except Exception:   # noqa: BLE001
        similarity = lambda a, b: 1.0 if a == b else 0.0   # noqa: E731
    line_tokens = {str(l["id"]): list(l.get("tokens") or []) for l in (script_lines or []) if l.get("tokens")}
    cues: List[Dict[str, Any]] = []
    for i, s in enumerate(segs):
        st, en = float(s["start"]), float(s["end"])
        t0 = float(starts[i])
        spoken = _speech_spans(s.get("src"))
        seg_line = _base_line(s.get("id", ""))
        words: List[Dict[str, Any]] = []
        for w in _words_of_clip(s.get("src")):
            text = str(w.get("w") or w.get("word") or "").strip()
            ws, we = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            mid = (ws + we) / 2.0
            if not text or mid < st or mid > en:
                continue
            if spoken and not any(a - 0.08 <= mid <= b + 0.08 for a, b in spoken):
                continue                      # a ghost word: nobody speaks there
            if line_tokens and _foreign_to_line(text, seg_line, line_tokens, similarity):
                continue
            words.append({"text": text, "start": t0 + max(0.0, ws - st), "end": t0 + min(en - st, max(0.0, we - st))})
        words.sort(key=lambda x: x["start"])
        cur: List[Dict[str, Any]] = []
        for k, w in enumerate(words):
            if cur and (len(cur) >= MAX_WORDS_PER_CUE or w["start"] - cur[-1]["end"] > MAX_GAP_IN_CUE or
                        (len(cur) >= 2 and SENTENCE_END.search(cur[-1]["text"]))):
                cues.append(_cue(cur))
                cur = []
            cur.append(w)
        if cur:
            cues.append(_cue(cur))
    return cues


def _cue(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    end = max(w["end"] for w in words)
    return {
        "startTime": round(words[0]["start"], 3),
        "endTime": round(end + 0.04, 3),
        "text": " ".join(w["text"] for w in words),
        "words": [{"text": w["text"], "startTime": round(w["start"], 3), "endTime": round(w["end"], 3)} for w in words],
    }


def render_frames(cues: List[Dict[str, Any]], style: Dict[str, Any], width: int, height: int, out_dir: Path, y_percent: float = 70.0) -> List[Dict[str, Any]]:
    """Draw every caption state to a transparent PNG with the Node renderer; returns [{path, startMs, durationMs, ...}]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {"width": width, "height": height, "styleId": style["id"], "outDir": str(out_dir), "positionY": y_percent, "cues": cues}
    jf = out_dir / "job.json"
    jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([node_path(), str(SUB_DIR / "render_cues.js"), str(jf)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"subtitle renderer failed: {(r.stderr or r.stdout).strip()[-400:]}")
    line = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    return json.loads(line)["frames"]


def write_concat_list(frames: List[Dict[str, Any]], total: float, width: int, height: int, out_dir: Path) -> Path:
    """An FFmpeg concat list covering 0..total: each caption PNG for its duration, a transparent PNG in the gaps."""
    from PIL import Image

    blank = out_dir / "blank.png"
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank)
    lines = ["ffconcat version 1.0"]
    t = 0.0
    last = str(blank)
    for f in sorted(frames, key=lambda x: x["startMs"]):
        a = max(t, f["startMs"] / 1000.0)
        b = max(a + 0.02, (f["startMs"] + f["durationMs"]) / 1000.0)
        if a - t > 0.001:
            lines += [f"file '{blank.as_posix()}'", f"duration {a - t:.4f}"]
        lines += [f"file '{Path(f['path']).as_posix()}'", f"duration {b - a:.4f}"]
        last = f["path"]
        t = b
    if total - t > 0.001:
        lines += [f"file '{blank.as_posix()}'", f"duration {total - t:.4f}"]
        last = str(blank)
    lines.append(f"file '{Path(last).as_posix()}'")     # the concat demuxer needs the last file repeated
    lst = out_dir / "subs.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def write_srt(cues: List[Dict[str, Any]], path: Path) -> None:
    def ts(x: float) -> str:
        ms = int(round(max(0.0, x) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{ts(c['startTime'])} --> {ts(c['endTime'])}", c["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- which style: rotate from job to job
HISTORY = "_subs_history.json"      # in work/, one list of the styles this computer used, newest last


def _history_file(work_dir: Path) -> Path:
    return Path(work_dir) / HISTORY


def recent_styles(work_dir: Optional[Path] = None, n: int = 3) -> List[str]:
    """The last `n` subtitle styles used on this computer, newest first."""
    from common import WORK

    try:
        d = json.loads(_history_file(work_dir or WORK).read_text(encoding="utf-8"))
        return [str(x.get("style")) for x in reversed(d.get("used", []))][:n]
    except Exception:   # noqa: BLE001
        return []


def remember_style(name: str, project: str, work_dir: Optional[Path] = None) -> None:
    from common import WORK

    f = _history_file(work_dir or WORK)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001
        d = {}
    used = [x for x in d.get("used", []) if x.get("project") != project]       # a re-render of the same edit does not count twice
    used.append({"style": name, "project": project, "at": time.strftime("%Y-%m-%d %H:%M")})
    try:
        f.write_text(json.dumps({"used": used[-20:]}, ensure_ascii=False), encoding="utf-8")
    except Exception:   # noqa: BLE001
        pass


def choose_style(project: str, work_dir: Optional[Path] = None, avoid: int = 2) -> Dict[str, Any]:
    """`#subs auto`: a style that is not one of the last `avoid` used on this computer, picked by a stable hash of the project name so a
    re-render of the same edit keeps its look while the next edit gets another one."""
    pool = styles()
    recent = set(recent_styles(work_dir, avoid))
    fresh = [s for s in pool if s["name"] not in recent] or pool
    h = sum(ord(c) * (k + 1) for k, c in enumerate(project or "zeno"))
    return fresh[h % len(fresh)]


# ---------------------------------------------------------------- colours: look at the picture behind each caption
SAFE_FILLS = {          # accents and box colours that stay readable (white text on them is 4.5:1 or better; bright ones sit on dark bars)
    "bright": ["#FACC15", "#22D3EE", "#A3E635", "#F472B6", "#FB923C", "#60A5FA", "#C084FC", "#FFFFFF"],
    "box": ["#2563EB", "#7C3AED", "#15803D", "#DC2626", "#DB2777", "#0F766E", "#C2410C"],
}


def _rgb(hexcol: str) -> Tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))     # type: ignore[return-value]


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
    """What the caption will sit on: mean luminance, the dominant hue and how colourful the band is. `img` is a small PIL RGB frame."""
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
        hue = (np.degrees(np.arctan2((np.sin(ang) * weights).sum(), (np.cos(ang) * weights).sum())) % 360.0)
    return {"lum": float(_lum(tuple(px.mean(axis=0)))), "hue": float(hue), "colour": float(colourful.mean()), "dark": float((lum < 0.25).mean())}


def adapt_style(style: Dict[str, Any], st: Dict[str, float]) -> Dict[str, Any]:
    """Colour overrides for one caption so it reads on the picture behind it. Bars and boxes stay dark and take a tint of the scene's own hue
    (they match it); the accent on the spoken word is the colour furthest from the scene's hue (it contrasts); outlined text switches colour when
    it would blend into the picture. Returns {} for styles whose colours need no change."""
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
        out["bgColor"] = dark_tint(0.78)
    elif kind_mode == "word-box":
        out["wordBoxColor"] = best(SAFE_FILLS["box"], vs_dark=False)
    elif kind_mode == "gradient-badge":
        c = best(SAFE_FILLS["box"], vs_dark=False)
        out["wordBoxColor"] = c
        out["activeColor"] = c
    elif kind_mode == "cinema":
        out["color"] = best(["#FFE600", "#FFFFFF", "#00E5FF", "#A3E635"], vs_dark=False)
        out["activeColor"] = out["color"]
    elif kind_mode == "comic-slap":
        out["activeColor"] = best(["#FACC15", "#22D3EE", "#A3E635", "#F472B6"], vs_dark=False)
    return out


def _frames_for_cues(video: Path, cues: List[Dict[str, Any]], out_dir: Path) -> List[Tuple[float, Any]]:
    """Small frames of the rendered picture, four a second, for measuring colours."""
    from PIL import Image
    from common import ffmpeg

    d = out_dir / "probe"
    d.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(video), "-vf", "fps=4,scale=108:192", "-f", "image2", str(d / "f%05d.png"), timeout=600)
    files = sorted(d.glob("f*.png"))
    return [(i / 4.0, Image.open(f).convert("RGB")) for i, f in enumerate(files)]


def build_overlay(plan: Dict[str, Any], segs: List[Dict[str, Any]], starts: List[float], total: float, width: int, height: int,
                  work_dir: Path, srt_path: Optional[Path] = None, video: Optional[Path] = None, report_dir: Optional[Path] = None) -> Optional[Path]:
    """Everything render.py needs: the concat list of caption images for the whole video, or None when subtitles are off or cannot be made
    (a problem is printed, the video still renders without them). With `video` (the picture rendered without captions) every caption's colours
    are chosen from the picture behind it; `#subs auto` also picks the style, rotating away from the recent ones."""
    spec = plan.get("subs")
    if not isinstance(spec, dict) or not spec.get("style"):
        return None
    if not ensure_ready():
        print("  [SUBS] Node.js is not installed (get the LTS version from nodejs.org) or its caption package could not be installed. Rendering without subtitles.")
        return None
    project = Path(str(plan.get("output") or "zeno")).stem
    if str(spec["style"]).strip().lower() == "auto":
        style = choose_style(project, work_dir)
    else:
        style = resolve_style(spec["style"])
    if style is None:
        print(f"  [SUBS] unknown style '{spec['style']}'. Styles: auto, " + ", ".join(f"{s['id']} {s['name']}" for s in styles()) + ". Rendering without subtitles.")
        return None
    script_lines: List[Dict[str, Any]] = []
    try:
        from prepare import parse_script_file
        from common import resolve

        if plan.get("script"):
            _, script_lines = parse_script_file(resolve(plan["script"]))
    except Exception:   # noqa: BLE001
        script_lines = []
    cues = build_cues(segs, starts, script_lines)
    if not cues:
        print("  [SUBS] no spoken words to show.")
        return None
    out_dir = work_dir / "_subs"
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    y = float(spec.get("y", 70.0))
    adapted = 0
    if video is not None and spec.get("adapt", True):
        try:
            probes = _frames_for_cues(Path(video), cues, out_dir)
            for c in cues:
                near = [im for t, im in probes if c["startTime"] - 0.15 <= t <= c["endTime"] + 0.15] or [min(probes, key=lambda p: abs(p[0] - c["startTime"]))[1]]
                stats = [band_stats(im, y) for im in near]
                avg = {k: sum(s[k] for s in stats) / len(stats) for k in stats[0]}
                avg["hue"] = stats[len(stats) // 2]["hue"]
                ov = adapt_style(style, avg)
                if ov:
                    c["style"] = ov
                    adapted += 1
            shutil.rmtree(out_dir / "probe", ignore_errors=True)
        except Exception as exc:   # noqa: BLE001  (the stored colours are always readable; adapting is a refinement)
            print(f"  [SUBS] could not read the picture's colours, using the style's own: {exc}")
    frames = render_frames(cues, style, width, height, out_dir, y)
    if srt_path is not None:
        write_srt(cues, srt_path)
    remember_style(style["name"], project, work_dir)
    if report_dir is not None:
        try:
            import job_report

            job_report.record_subs(report_dir, style["name"], len(cues), adapted)
        except Exception:   # noqa: BLE001
            pass
    print(f"  [SUBS] {len(cues)} captions, {len(frames)} frames, style '{style['name']}', colours adapted on {adapted} captions")
    try:
        import events

        events.emit("subs", f"Subtitles: {len(cues)} captions in '{style['name']}', colours adapted to the picture on {adapted}", style=style['name'], captions=len(cues))
    except Exception:
        pass
    return write_concat_list(frames, total, width, height, out_dir)
