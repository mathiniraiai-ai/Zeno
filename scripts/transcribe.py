"""Probe footage clips, extract 16 kHz audio, transcribe & force-align words, and detect silences.

Usage:
    python3 scripts/transcribe.py input/footage/*.mp4 [--lang auto] [--script input/script/script.txt]

Writes into each clip's cache folder (`work/_clips/<stem>-<hash>/`):
    info.json        duration, resolution, frame rate, audio metadata
    words.json       [{"w": "hello", "start": 1.23, "end": 1.51, "p": 0.93}, ...]
    transcript.txt   timestamped phrases for quick script comparison
    silences.json    [{"start": .., "end": ..}, ...] pauses >= 0.35 s
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    IS_WIN,
    clip_work_dir,
    ffmpeg,
    find_venv_python,
    probe,
    read_json,
    resolve,
    tc,
    write_json,
)

SILENCE_DB = -35
SILENCE_MIN = 0.35
PHRASE_GAP = 0.45


def _ensure_venv_if_needed() -> None:
    """Re-exec under `find_venv_python()` if `torch` is unavailable in the invoking interpreter."""
    try:
        import torch  # noqa: F401
    except ImportError:
        if os.environ.get("_ZENO_REEXEC") == "1":
            return
        venv_py = find_venv_python()
        if venv_py and Path(venv_py).is_file():
            os.environ["_ZENO_REEXEC"] = "1"
            if IS_WIN:
                sys.exit(subprocess.call([str(venv_py), *sys.argv]))
            else:
                os.execv(str(venv_py), [str(venv_py), *sys.argv])


def detect_silences(wav: Path | str) -> List[Dict[str, float]]:
    """Detect silent spans (`<= -35 dB` for `>= 0.35 s`) using FFmpeg `silencedetect`."""
    r = ffmpeg("-i", wav, "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [{"start": round(max(s, 0.0), 3), "end": round(e, 3)} for s, e in zip(starts, ends)]


def phrases(words: List[Dict[str, Any]]) -> List[Tuple[float, float, str]]:
    """Group aligned word dicts into readable phrases separated by pauses or sentence punctuation."""
    out: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > PHRASE_GAP or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [(p[0]["start"], p[-1]["end"], " ".join(w["w"] for w in p)) for p in out]


def main() -> None:
    _ensure_venv_if_needed()
    from align_engine import transcribe_and_align_clip

    ap = argparse.ArgumentParser(description="Transcribe and force-align footage clips.")
    ap.add_argument("clips", nargs="+", help="Input footage paths or glob patterns")
    ap.add_argument("--lang", default="auto", help="Spoken language code (e.g. en, kn, ta, te, ml, hi, or auto)")
    ap.add_argument("--script", help="Optional script file used for vocabulary hints and alignment")
    args = ap.parse_args()

    script_path = None
    if args.script:
        resolved_script = resolve(args.script)
        if resolved_script.is_file():
            script_path = str(resolved_script)

    clips = [c for pat in args.clips for c in (glob.glob(str(resolve(pat))) or [pat])]
    for raw_clip in clips:
        clip = resolve(raw_clip)
        d = clip_work_dir(clip)
        d.mkdir(parents=True, exist_ok=True)
        wav = d / "audio16k.wav"
        if clip.is_file():
            info = probe(clip)
            write_json(d / "info.json", info)
            print(f"{clip.name}: {tc(info['duration'] or 0)}  {info['width']}x{info['height']} @ {info['fps']} fps")
            if not info["has_audio"]:
                print("  no audio track, skipped transcription")
                continue
            ffmpeg("-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav)
        elif (d / "info.json").is_file() and wav.is_file():
            info = read_json(d / "info.json")
            print(f"{clip.stem}: {tc(info.get('duration') or 0)} (using existing audio16k.wav)")
        else:
            print(f"{clip}: clip file and {wav} not found, skipped")
            continue

        res = transcribe_and_align_clip(
            wav_path=wav,
            lang=args.lang,
            script_lines=script_path,
            work_dir=d,
        )
        words = res.get("words", [])
        lang = res.get("language", args.lang)
        tail_noise = res.get("tail_noise", [])

        write_json(d / "words.json", words)
        if tail_noise:
            write_json(d / "tail_noise.json", tail_noise)
        write_json(d / "silences.json", detect_silences(wav))

        lines = [f"# {clip.name}  language={lang}  words={len(words)}"]
        lines += [f"[{tc(s)} - {tc(e)}]  ({s:.2f}-{e:.2f})  {t}" for s, e, t in phrases(words)]
        for tn in tail_noise:
            lines.append(f"[{tc(tn['start'])} - {tc(tn['end'])}]  ({tn['start']:.2f}-{tn['end']:.2f})  [TAIL_BABBLE]")
        (d / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {len(words)} words, language={lang} -> {d / 'transcript.txt'}")


if __name__ == "__main__":
    main()
