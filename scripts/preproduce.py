#!/usr/bin/env python3
"""Pre-production for AI ad reels: turn a script into one Veo 3.1 shot per line, before any clip exists.

    python3 scripts/preproduce.py init  <script> --project W-44_ML_ABC [--lang ml]   # shot skeletons + character sheet
    python3 scripts/preproduce.py apply <folder>                                      # copy each character into their shots
    python3 scripts/preproduce.py check <folder>                                      # validate everything, write shots.md

The script (and the checks) do the exact parts: numbering D-1, D-2..., the dialogue copied word for word, the fixed JSON
schema, one speaking subject per shot, the same character description and voice in every shot of that character, the
clip file names Zeno matches later, and a warning when a line is too long for one 8-second clip.
The agent writes the creative parts: the character sheet, and each shot's style, camera, light, expression, action,
voice tone and first-frame prompt (see agent/concepts/08_preproduction/CONCEPT.md).

Folder created (in Downloads/Zeno Outputs/<project>_preproduction/):
    script.md              D-1  Man: exact line  (note)       <- what Zeno reads later
    characters.json        one entry per speaker: look, outfit, voice (fill once, reused in every shot)
    shots/D-1.json ...     one exact-schema JSON per shot (this is what you paste into Veo)
    frames/D-1.txt ...     the first-frame prompt per shot (text; fill)
    shots.md               everything together for copy-paste (written by `check`)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT  # noqa: E402

# The exact Veo 3.1 shot schema. Keys, nesting and order never change.
SHOT_SCHEMA: Dict[str, Any] = {
    "style": "",
    "composition": "",
    "camera_motion": "",
    "lighting_mood": "",
    "film_grain": "none",
    "subject": {
        "description": {"age": "", "ethnicity": "", "skin_tone": "", "attire": "", "expression": ""},
        "action": "",
    },
    "audio": {
        "dialogue": "",
        "voice": {"tone": "", "accent": "", "nationality": "", "gender": "", "speed": 1.0, "style_tags": [], "pitch": ""},
    },
    "negative_prompt": "",
}
# These sub-blocks are the same for every shot of a character (continuity); expression and action change per shot.
CHARACTER_FIELDS = ("age", "ethnicity", "skin_tone", "attire")
VOICE_FIELDS = ("accent", "nationality", "gender", "pitch")
MAX_SPEECH_SECONDS = 7.5          # one Veo clip is 8 s
WORDS_PER_SECOND = 2.5            # about 150 words a minute; Indian-language lines run slower, so this errs on the safe side
OPTIONAL_EMPTY = {("film_grain",)}  # film_grain may stay "none"; nothing else may be empty


def parse_script(path: Path) -> List[Dict[str, Any]]:
    """Script lines with speaker, exact dialogue, note and beat flag (same grammar prepare.py reads)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prepare
    _, lines = prepare.parse_script_file(path)
    return lines


def out_dir(project: str) -> Path:
    return OUTPUT / f"{project}_preproduction"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def split_points(text: str) -> List[str]:
    """Sentence ends where a long line could be cut into two clips."""
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p for p in parts if p]


def speech_seconds(text: str) -> float:
    return len(re.findall(r"[\w\u0900-\u0D7F']+", text)) / WORDS_PER_SECOND


def cmd_init(a: argparse.Namespace) -> int:
    script = Path(a.script)
    lines = parse_script(script)
    if not lines:
        print("No lines found in the script.")
        return 1
    d = out_dir(a.project)
    if d.exists() and any(d.iterdir()) and not a.force:
        print(f"{d} already has files. Use --force to start over (this overwrites the shots).")
        return 1
    (d / "shots").mkdir(parents=True, exist_ok=True)
    (d / "frames").mkdir(parents=True, exist_ok=True)

    speakers: List[str] = []
    for l in lines:
        if l.get("speaker") and l["speaker"] not in speakers:
            speakers.append(l["speaker"])
    chars = {
        sp: {
            "age": "", "ethnicity": "", "skin_tone": "", "attire": "",
            "voice": {"accent": "", "nationality": "", "gender": "", "pitch": ""},
            "voice_default_tone": "",
        }
        for sp in speakers
    }
    _write_json(d / "characters.json", chars)

    script_md: List[str] = []
    warnings: List[str] = []
    for n, l in enumerate(lines, start=1):
        shot = copy.deepcopy(SHOT_SCHEMA)
        shot["audio"]["dialogue"] = "" if l.get("beat") else l["text"]
        if l.get("note"):
            shot["subject"]["action"] = l["note"]
        _write_json(d / "shots" / f"D-{n}.json", shot)
        (d / "frames" / f"D-{n}.txt").write_text("", encoding="utf-8")
        if l.get("beat"):
            script_md.append(f"D-{n}  [beat] {l['text']}")
        else:
            note = f"  ({l['note']})" if l.get("note") else ""
            script_md.append(f"D-{n}  {l['speaker'] or '?'}: {l['text']}{note}")
            secs = speech_seconds(l["text"])
            if secs > MAX_SPEECH_SECONDS:
                pts = split_points(l["text"])
                warnings.append(f"D-{n} is about {secs:.0f}s of speech, too long for one 8 s clip. "
                                + (f"Split it at a sentence end ({len(pts)} sentences)." if len(pts) > 1 else "Shorten it or split it with the writer."))
    (d / "script.md").write_text("\n".join(script_md) + "\n", encoding="utf-8")
    (d / "reel.json").write_text(json.dumps({"project": a.project, "lang": a.lang, "reference": a.reference or "",
                                              "clips": [f"{a.project}_D-{n}.mp4" for n in range(1, len(lines) + 1)]}, indent=2) + "\n", encoding="utf-8")
    print(f"Created {len(lines)} shots in {d}")
    print(f"  Speakers to describe in characters.json: {', '.join(speakers) or '(none named in the script)'}")
    print(f"  Clip names to save from Flow: {a.project}_D-1.mp4 ... {a.project}_D-{len(lines)}.mp4")
    for w in warnings:
        print(f"  ! {w}")
    return 0


def cmd_apply(a: argparse.Namespace) -> int:
    """Copy each character's fixed look and voice into every shot of that character."""
    d = Path(a.folder)
    chars = json.loads((d / "characters.json").read_text(encoding="utf-8"))
    script = [ln for ln in (d / "script.md").read_text(encoding="utf-8").splitlines() if ln.strip()]
    n_done = 0
    for ln in script:
        m = re.match(r"^D-(\d+)\s+([^:\[]+?):\s", ln)
        beat = re.match(r"^D-(\d+)\s+\[beat\]\s+(.*)$", ln)
        if m:
            n, sp, is_beat = int(m.group(1)), m.group(2).strip(), False
        elif beat:   # "(Man takes out his phone)": the character named first is the subject
            n, is_beat = int(beat.group(1)), True
            sp = next((name for name in chars if re.match(rf"^{re.escape(name)}\b", beat.group(2), re.I)), "")
        else:
            continue
        c = chars.get(sp)
        if not c:
            continue
        path = d / "shots" / f"D-{n}.json"
        shot = json.loads(path.read_text(encoding="utf-8"))
        for f in CHARACTER_FIELDS:
            shot["subject"]["description"][f] = c.get(f, "")
        if not is_beat:
            for f in VOICE_FIELDS:
                shot["audio"]["voice"][f] = c.get("voice", {}).get(f, "")
            if c.get("voice_default_tone") and not shot["audio"]["voice"]["tone"]:
                shot["audio"]["voice"]["tone"] = c["voice_default_tone"]
        _write_json(path, shot)
        n_done += 1
    print(f"Applied character sheets to {n_done} shot(s).")
    return 0


def _walk(schema: Any, shot: Any, path: Tuple[str, ...], errors: List[str], label: str, relaxed: Tuple[str, ...] = ()) -> None:
    if isinstance(schema, dict):
        if not isinstance(shot, dict):
            errors.append(f"{label}: '{'.'.join(path)}' must be an object")
            return
        if list(shot.keys()) != list(schema.keys()):
            extra = [k for k in shot if k not in schema]
            miss = [k for k in schema if k not in shot]
            errors.append(f"{label}: '{'.'.join(path) or 'shot'}' keys must be exactly {list(schema)} (extra {extra}, missing {miss}, in this order)")
            return
        for k in schema:
            _walk(schema[k], shot[k], path + (k,), errors, label, relaxed)
        return
    if isinstance(schema, list):
        if not isinstance(shot, list):
            errors.append(f"{label}: '{'.'.join(path)}' must be a list")
        return
    if isinstance(schema, float):
        if not isinstance(shot, (int, float)) or isinstance(shot, bool) or shot <= 0:
            errors.append(f"{label}: '{'.'.join(path)}' must be a number above 0")
        return
    if not isinstance(shot, str):
        errors.append(f"{label}: '{'.'.join(path)}' must be text")
    elif not shot.strip() and path not in OPTIONAL_EMPTY and path[: len(relaxed)] != relaxed:
        errors.append(f"{label}: '{'.'.join(path)}' is empty")


def cmd_check(a: argparse.Namespace) -> int:
    d = Path(a.folder)
    meta = json.loads((d / "reel.json").read_text(encoding="utf-8"))
    script = [ln for ln in (d / "script.md").read_text(encoding="utf-8").splitlines() if ln.strip()]
    chars = json.loads((d / "characters.json").read_text(encoding="utf-8"))
    errors: List[str] = []
    warnings: List[str] = []
    shots: Dict[int, Dict[str, Any]] = {}
    by_speaker: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}

    for ln in script:
        m = re.match(r"^D-(\d+)\s+(.*)$", ln)
        if not m:
            continue
        n, rest = int(m.group(1)), m.group(2)
        label = f"D-{n}"
        is_beat = rest.startswith("[beat]")
        if is_beat:
            speaker, dialogue = "", ""
        else:
            ms = re.match(r"^([^:]+?):\s(.*?)(?:\s{2}\(([^)]*)\))?$", rest)
            speaker, dialogue = (ms.group(1).strip(), ms.group(2).strip()) if ms else ("?", rest)
        path = d / "shots" / f"D-{n}.json"
        if not path.is_file():
            errors.append(f"{label}: shots/D-{n}.json is missing")
            continue
        try:
            shot = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{label}: not valid JSON ({exc})")
            continue
        _walk(SHOT_SCHEMA, shot, (), errors, label, relaxed=("audio",) if is_beat else ())   # a silent beat needs no voice
        if not isinstance(shot, dict) or "audio" not in shot:
            continue
        shots[n] = shot
        got = str(shot.get("audio", {}).get("dialogue", ""))
        if got != dialogue:
            errors.append(f"{label}: dialogue must be the script line word for word. Script: {dialogue!r} / shot: {got!r}")
        if is_beat:
            if shot["audio"]["dialogue"]:
                errors.append(f"{label}: a beat shot has no dialogue")
        else:
            secs = speech_seconds(dialogue)
            if secs > MAX_SPEECH_SECONDS:
                warnings.append(f"{label}: about {secs:.0f}s of speech is too long for one 8 s clip; split it at a sentence end")
            if re.search(r"\.\.\.|…", dialogue):
                warnings.append(f"{label}: the line has an ellipsis; Veo may add pauses there")
            by_speaker.setdefault(speaker, []).append((n, shot))
            if speaker not in chars:
                errors.append(f"{label}: speaker {speaker!r} is not in characters.json")
        frame = d / "frames" / f"D-{n}.txt"
        if not frame.is_file() or not frame.read_text(encoding="utf-8").strip():
            errors.append(f"{label}: frames/D-{n}.txt (the first-frame prompt) is empty")
        if shot.get("film_grain") not in ("none", ""):
            warnings.append(f"{label}: film_grain is {shot['film_grain']!r}; the house default is 'none'")

    # Continuity: the same character must look and sound the same in every shot
    for sp, items in by_speaker.items():
        first_n, first = items[0]
        for n, sh in items[1:]:
            for f in CHARACTER_FIELDS:
                if sh["subject"]["description"][f] != first["subject"]["description"][f]:
                    errors.append(f"D-{n}: {sp}'s {f} differs from D-{first_n}. Run `apply` to copy the character sheet into every shot")
            for f in VOICE_FIELDS + ("speed",):
                if sh["audio"]["voice"][f] != first["audio"]["voice"][f]:
                    errors.append(f"D-{n}: {sp}'s voice {f} differs from D-{first_n}. Run `apply` (speed is set per shot only if needed)")

    # shots.md for copy-paste
    md = [f"# {meta['project']} shots", "", f"Save each generated clip as `{meta['project']}_D-<n>.mp4` in one folder, then run Zeno on that folder.", ""]
    for n in sorted(shots):
        md += [f"## D-{n}  ({meta['project']}_D-{n}.mp4)", "", "**First frame prompt**", "",
               (d / "frames" / f"D-{n}.txt").read_text(encoding="utf-8").strip() if (d / "frames" / f"D-{n}.txt").is_file() else "",
               "", "**Veo shot JSON**", "", "```json", json.dumps(shots[n], indent=2, ensure_ascii=False), "```", ""]
    (d / "shots.md").write_text("\n".join(md), encoding="utf-8")

    for w in warnings:
        print(f"  warning: {w}")
    for e in errors:
        print(f"  ERROR: {e}")
    if errors:
        print(f"{len(errors)} problem(s). Fix them and run check again.")
        return 1
    print(f"OK: {len(shots)} shot(s) valid. Paste from {d / 'shots.md'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("script")
    p.add_argument("--project", required=True, help="reel code, e.g. W-44_ML_ABC; it names the clips")
    p.add_argument("--lang", default="auto")
    p.add_argument("--reference", default="", help="path to the reference image/video (noted for the agent)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("apply")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("check")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
