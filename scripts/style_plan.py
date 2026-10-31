#!/usr/bin/env python3
"""Build a plan.json for a learned style from an ordered clip manifest. Reads styles/<style>/spec.json ("engine" block).

Usage:
    python3 scripts/style_plan.py micronarrative input/standins/micronarrative/manifest.json \
        --out work/micronarrative_rebuild/plan.json [--canvas 720x1280@24]

Manifest: a list of {"name": "s1_wide", "file": "path.mp4", "frames": 53}. The name prefix `s<scene>_` gives the scene;
in a split scene, clips ending `_left` / `_right` become the two panels. Clips are used in manifest order.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402


def build(style: str, manifest: list, canvas: dict, output: str, fps_src: float = 24.0) -> dict:
    spec = json.loads((ROOT / "styles" / style / "spec.json").read_text(encoding="utf-8"))
    eng = spec["engine"]
    scene_layouts = eng["scene_layouts"]
    swaps = eng["scene_transition"]["swap_frames"]
    scenes: dict = {}
    for c in manifest:
        m = re.match(r"s(\d+)_(.+)", c["name"])
        scenes.setdefault(int(m.group(1)), []).append({**c, "tag": m.group(2)})

    segments = []
    for sc in sorted(scenes):
        clips = scenes[sc]
        layout = scene_layouts[str(sc)]
        first_of_scene = True
        if layout == "split_h":
            left = next(c for c in clips if c["tag"].endswith("left"))
            right = next(c for c in clips if c["tag"].endswith("right"))
            dur = left["frames"] / fps_src
            seg = {"src": left["file"], "start": 0.0, "end": round(dur, 4), "layout": "split_h", "slot": "left",
                   "peer": {"src": right["file"], "mode": "live", "start": 0.0}, "focus": [0.5, 0.36],
                   "framing": "exact", "divider": eng["split"]["divider"],
                   "note": f"scene {sc} split: {eng['split']['left']} | {eng['split']['right']}"}
            group = [seg]
        else:
            group = [{"src": c["file"], "start": 0.0, "end": round(c["frames"] / fps_src, 4), "layout": layout,
                      "focus": [0.5, 0.36], "note": f"scene {sc} {c['tag']}"} for c in clips]
        for seg in group:
            if first_of_scene and sc > 1:
                seg["transition"] = eng["scene_transition"]["name"]
                seg["transition_swap"] = swaps[min(sc - 2, len(swaps) - 1)]
            first_of_scene = False
            segments.append(seg)
    for i, s in enumerate(segments, 1):
        s["id"] = f"S{i}"
    return {"output": output, "canvas": canvas, "preset": "hybrid", "segments": segments,
            "marks": [], "sfx": [], "audio": {"loudness": -14}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("style")
    ap.add_argument("manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--canvas", default=None, help="WxH@fps, default from the style spec")
    ap.add_argument("--output", default=None, help="final mp4 path inside plan")
    a = ap.parse_args()
    spec = json.loads((ROOT / "styles" / a.style / "spec.json").read_text(encoding="utf-8"))
    cv = spec["engine"]["canvas_default"]
    if a.canvas:
        m = re.match(r"(\d+)x(\d+)@(\d+)", a.canvas)
        cv = {"width": int(m.group(1)), "height": int(m.group(2)), "fps": int(m.group(3))}
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    plan = build(a.style, manifest, cv, a.output or f"output/{a.style}_rebuild.mp4")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(f"{len(plan['segments'])} segments -> {out}")
    for s in plan["segments"]:
        extra = f" [{s['transition']} swap {s['transition_swap']}]" if s.get("transition") else ""
        print(f"  {s['id']:<4} {s['layout']:<8} {s['end'] - s['start']:.3f}s{extra}  {s['note']}")


if __name__ == "__main__":
    main()
