"""Burn-in subtitles for a rendered edit, only when the plan asks for them (`#subs <style> [y=70]` in plan.edl).

The words come from what the clips actually say (the aligned transcript, with each word's time), shifted onto the finished timeline.
They are never translated: Kannada stays Kannada, English stays English. The pictures are drawn by Edit Flow AI's caption renderer
(`subtitles/caption-renderer.js`, ten styles in `subtitles/styles.json`) running on node-canvas, one transparent PNG per spoken word state.
render.py overlays them in the final mix as one image sequence. Standard library only, plus Node for the drawing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
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


def build_overlay(plan: Dict[str, Any], segs: List[Dict[str, Any]], starts: List[float], total: float, width: int, height: int,
                  work_dir: Path, srt_path: Optional[Path] = None) -> Optional[Path]:
    """Everything render.py needs: the concat list of caption images for the whole video, or None when subtitles are off or cannot be made
    (a problem is printed, the video still renders without them)."""
    spec = plan.get("subs")
    if not isinstance(spec, dict) or not spec.get("style"):
        return None
    style = resolve_style(spec["style"])
    if style is None:
        print(f"  [SUBS] unknown style '{spec['style']}'. Styles: " + ", ".join(f"{s['id']} {s['name']}" for s in styles()) + ". Rendering without subtitles.")
        return None
    if not ensure_ready():
        print("  [SUBS] Node.js is not installed (get the LTS version from nodejs.org) or its caption package could not be installed. Rendering without subtitles.")
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
    frames = render_frames(cues, style, width, height, out_dir, float(spec.get("y", 70.0)))
    if srt_path is not None:
        write_srt(cues, srt_path)
    print(f"  [SUBS] {len(cues)} captions, {len(frames)} frames, style '{style['name']}'")
    return write_concat_list(frames, total, width, height, out_dir)
