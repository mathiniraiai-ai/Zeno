#!/usr/bin/env python3
"""Measure a finished reference video so a style can be learned from it. Python reads every frame; the agent reads only
the small JSON and the contact sheets this writes.

Usage:
    python3 scripts/learn_reference.py scan input/reference/<video>.mp4 --name <style>
        -> work/<style>_ref/scan.json        per-frame change score, shots, face tracks (4 fps)
        -> work/<style>_ref/shots_NN.jpg     contact sheets, one tile per shot (first frame, time-stamped)
"""
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import WORK, YUNET_MODEL, ffmpeg, read_script_text  # noqa: E402

SMALL = (90, 160)          # w, h used for change scores
FACE_FPS = 4               # face detection rate
TILE = (160, 284)          # contact-sheet tile (w, h)
COLS = 8


def _label(img, text):
    cv2.rectangle(img, (0, 0), (img.shape[1], 16), (0, 0, 0), -1)
    cv2.putText(img, text, (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def _sheet(tiles, out_prefix, per_sheet=48):
    paths = []
    for page, start in enumerate(range(0, len(tiles), per_sheet)):
        chunk = tiles[start:start + per_sheet]
        rows = []
        for r in range(0, len(chunk), COLS):
            row = chunk[r:r + COLS]
            row += [np.zeros_like(chunk[0])] * (COLS - len(row))
            rows.append(np.hstack(row))
        path = f"{out_prefix}_{page + 1:02d}.jpg"
        cv2.imwrite(path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])
        paths.append(path)
    return paths


def scan(video: Path, name: str, cut_threshold: float):
    out_dir = WORK / f"{name}_ref"
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    detector = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (w, h), 0.7, 0.3, 5000)
    face_every = max(1, round(fps / FACE_FPS))

    prev_small, prev_hist = None, None
    diffs, hist_d, faces, thumbs = [], [], [], {}
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.cvtColor(cv2.resize(frame, SMALL, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([cv2.resize(frame, SMALL)], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        diffs.append(0.0 if prev_small is None else float(np.mean(cv2.absdiff(small, prev_small))))
        hist_d.append(0.0 if prev_hist is None else float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)))
        prev_small, prev_hist = small, hist
        thumbs[i] = cv2.resize(frame, TILE, interpolation=cv2.INTER_AREA) if (i == 0 or diffs[-1] > cut_threshold * 0.5 or hist_d[-1] > 0.25) else None
        if i % face_every == 0:
            _, det = detector.detect(frame)
            boxes = []
            if det is not None:
                for d in det:
                    boxes.append([round(float(v), 1) for v in d[:4]] + [round(float(d[14]), 2)])
            faces.append({"f": i, "t": round(i / fps, 3), "faces": boxes})
        i += 1
    cap.release()
    n = i

    # A cut is a big jump in pixel change that also changes the colour histogram.
    raw_cuts = [k for k in range(1, n) if diffs[k] > cut_threshold and hist_d[k] > 0.2]
    groups, cur = [], []
    for k in raw_cuts:
        if cur and (k - cur[-1]) / fps > 0.6:
            groups.append(cur)
            cur = []
        cur.append(k)
    if cur:
        groups.append(cur)
    plain_cuts, transitions = [], []
    for g in groups:
        if len(g) >= 3:        # a burst of changes = a transition effect, not a cut
            transitions.append({"start": round(g[0] / fps, 3), "end": round(g[-1] / fps, 3),
                                "center": round((g[0] + g[-1]) / 2 / fps, 3), "frames": len(g)})
        else:
            plain_cuts.extend(g)
    bounds = sorted(plain_cuts + [round((t["start"] + t["end"]) / 2 * fps) for t in transitions])
    shots = []
    starts = [0] + bounds
    for s_i, s_f in enumerate(starts):
        e_f = (starts[s_i + 1] if s_i + 1 < len(starts) else n)
        shots.append({"id": s_i + 1, "start_f": s_f, "end_f": e_f, "start": round(s_f / fps, 3), "end": round(e_f / fps, 3),
                      "dur": round((e_f - s_f) / fps, 3)})

    cap = cv2.VideoCapture(str(video))
    tiles = []
    for sh in shots:
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(sh["start_f"] + 2, n - 1))
        ok, fr = cap.read()
        if ok:
            tiles.append(_label(cv2.resize(fr, TILE, interpolation=cv2.INTER_AREA), f"#{sh['id']} {sh['start']:.1f}s"))
    cap.release()
    sheets = _sheet(tiles, str(out_dir / "shots"))

    data = {"video": str(video), "fps": fps, "width": w, "height": h, "frames": n, "duration": round(n / fps, 3),
            "cut_threshold": cut_threshold, "shots": shots,
            "plain_cuts": [round(k / fps, 3) for k in plain_cuts], "transitions": transitions,
            "diff": [round(d, 2) for d in diffs], "hist": [round(d, 3) for d in hist_d], "face_track": faces}
    (out_dir / "scan.json").write_text(json.dumps(data), encoding="utf-8")
    print(f"{n} frames, {len(shots)} shots, {len(sheets)} contact sheet(s) -> {out_dir}")
    for p in sheets:
        print("  ", p)
    durs = [s["dur"] for s in shots]
    print(f"shot length: min {min(durs):.2f}s  median {float(np.median(durs)):.2f}s  max {max(durs):.2f}s")


def frames(video: Path, name: str, start: float, end: float, step: float, tag: str, cols: int = 8):
    """Contact sheet of frames from start to end every `step` seconds (time-stamped)."""
    global COLS
    COLS = cols
    out_dir = WORK / f"{name}_ref"
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    tiles, t = [], start
    while t <= end + 1e-6:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, fr = cap.read()
        if ok:
            tiles.append(_label(cv2.resize(fr, TILE, interpolation=cv2.INTER_AREA), f"{t:.2f}s"))
        t += step
    cap.release()
    for pth in _sheet(tiles, str(out_dir / tag), per_sheet=64):
        print(pth)


SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z0-9 .'\-]{0,30}?)\s*:\s*(.+)$")
SCENE_RE = re.compile(r"^\s*SCENE\s+(\d+)\b(.*)$", re.I)
DIRECTION_RE = re.compile(r"\(([^)]*)\)")


def dialogue(text: str) -> list:
    """Dialogue lines from a script tab: `Speaker: text`, with scene numbers and (stage directions) split out."""
    lines, scene, scene_title, notes = [], 0, "", []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("# source:"):
            continue
        m = SCENE_RE.match(s)
        if m:
            scene, scene_title, notes = int(m.group(1)), m.group(2).strip(" —-"), []
            continue
        m = SPEAKER_RE.match(s)
        if m and len(m.group(1).split()) <= 4 and not m.group(1).lower().startswith(("visual", "http")):
            spoken = m.group(2)
            directions = [d.strip() for d in DIRECTION_RE.findall(spoken)]
            spoken = re.sub(r"\s+", " ", DIRECTION_RE.sub("", spoken)).strip()
            if spoken:
                lines.append({"id": f"L{len(lines) + 1}", "scene": scene, "scene_title": scene_title,
                              "speaker": m.group(1).strip(), "text": spoken, "directions": directions,
                              "notes": notes})
                notes = []
            continue
        if scene:
            notes.append(s)
    return lines


def _tokens(text: str) -> list:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from align_engine.script_matcher import normalize_phonetic_tokens
    return normalize_phonetic_tokens(text)


def align(video: Path, name: str, script: Path, lang: str):
    """Transcribe (Gemini) + align (MMS) the reference audio, then map each script line to a time span."""
    out_dir = WORK / f"{name}_ref"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "audio16k.wav"
    ffmpeg("-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav))

    lines = dialogue(read_script_text(script))
    scan_json = json.loads((out_dir / "scan.json").read_text(encoding="utf-8"))
    duration = scan_json["duration"]
    scene_ids = sorted({l["scene"] for l in lines})
    edges = [0.0] + [t["center"] for t in scan_json["transitions"]][: len(scene_ids) - 1] + [duration]
    import soundfile as sf
    audio, sr = sf.read(str(wav), dtype="float32")
    from align_engine import transcribe_and_align_clip
    words = []
    for i, sc in enumerate(scene_ids):
        t0, t1 = edges[i], edges[i + 1]
        seg = out_dir / f"scene{sc}.wav"
        sf.write(str(seg), audio[int(t0 * sr):int(t1 * sr)], sr)
        sc_lines = [l for l in lines if l["scene"] == sc]
        has_ml = any(re.search(r"[\u0D00-\u0D7F]", l["text"]) for l in sc_lines)
        sc_lang = lang if has_ml else "en"
        res = transcribe_and_align_clip(seg, lang=sc_lang, script_lines=[l["text"] for l in sc_lines], work_dir=out_dir)
        for w in res.get("words", []):
            w["start"], w["end"] = round(w["start"] + t0, 3), round(w["end"] + t0, 3)
            w["scene"] = sc
            words.append(w)
    (out_dir / "words.json").write_text(json.dumps({"words": words, "scene_edges": edges}, ensure_ascii=False), encoding="utf-8")

    # Match script tokens to transcript tokens in order, then take each line's first/last matched word time.
    s_tok, s_line = [], []
    for li, l in enumerate(lines):
        for t in _tokens(l["text"]):
            s_tok.append(t)
            s_line.append(li)
    w_tok, w_idx = [], []
    for wi, w in enumerate(words):
        for t in _tokens(w.get("w", "")):
            w_tok.append(t)
            w_idx.append(wi)
    sm = SequenceMatcher(None, s_tok, w_tok, autojunk=False)
    hits = {li: [] for li in range(len(lines))}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            hits[s_line[a + k]].append(w_idx[b + k])
    total = {li: sum(1 for x in s_line if x == li) for li in range(len(lines))}
    for li, l in enumerate(lines):
        idx = sorted(set(hits[li]))
        if idx:
            l["start"], l["end"] = round(words[idx[0]]["start"], 2), round(words[idx[-1]]["end"], 2)
        else:
            l["start"] = l["end"] = None
        l["match"] = round(len(hits[li]) / max(1, total[li]), 2)
    (out_dir / "map.json").write_text(json.dumps({"language": lang, "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(words)} words aligned, {len(lines)} script lines mapped -> {out_dir / 'map.json'}")
    for l in lines:
        print(f"  {l['id']:>3} S{l['scene']} {l['speaker'][:12]:<12} {l['start']}-{l['end']}  match={l['match']}")


def main():
    ap = argparse.ArgumentParser(description="Measure a reference video")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("video")
    p.add_argument("--name", required=True)
    p.add_argument("--cut-threshold", type=float, default=25.0)
    a = sub.add_parser("align")
    a.add_argument("video")
    a.add_argument("--name", required=True)
    a.add_argument("--script", required=True)
    a.add_argument("--lang", default="ml")
    f = sub.add_parser("frames")
    f.add_argument("video")
    f.add_argument("--name", required=True)
    f.add_argument("--start", type=float, required=True)
    f.add_argument("--end", type=float, required=True)
    f.add_argument("--step", type=float, required=True)
    f.add_argument("--tag", required=True)
    f.add_argument("--cols", type=int, default=8)
    args = ap.parse_args()
    if args.cmd == "align":
        align(Path(args.video), args.name, Path(args.script), args.lang)
    elif args.cmd == "frames":
        frames(Path(args.video), args.name, args.start, args.end, args.step, args.tag, args.cols)
    elif args.cmd == "scan":
        scan(Path(args.video), args.name, args.cut_threshold)


if __name__ == "__main__":
    main()
