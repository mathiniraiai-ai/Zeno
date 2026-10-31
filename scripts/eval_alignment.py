#!/usr/bin/env python3
"""Score word timing against hand-marked boundaries. This is how we find out whether an alignment change helped.

Gold file (JSON): {"audio": "gold/audio/x.wav", "language": "ml", "words": [{"w": "word", "start": 1.23, "end": 1.58}, ...]}

Usage:
    python3 scripts/eval_alignment.py score gold/*.json [--no-snap]
    python3 scripts/eval_alignment.py from-labels labels.txt --audio x.wav --lang ml --out gold/x.json

`from-labels` reads an Audacity label export (tab-separated: start, end, word), so a person can mark word
boundaries in Audacity (Edit > Labels), export them, and get a gold file. Mark about 100-200 words from real footage,
across the languages and voices the team uses. `--no-snap` turns off the energy start snap to compare with and without it.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402


def from_labels(labels: Path, audio: str, lang: str, out: Path):
    words = []
    for line in labels.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and not parts[0].startswith("\\"):
            words.append({"w": parts[2].strip(), "start": round(float(parts[0]), 3), "end": round(float(parts[1]), 3)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"audio": audio, "language": lang, "words": words}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(words)} words -> {out}")


def summarize(name: str, errs: np.ndarray):
    if len(errs) == 0:
        return f"{name}: no data"
    a = np.abs(errs)
    return (f"{name}: n={len(a)} median {np.median(a) * 1000:.0f} ms, mean {a.mean() * 1000:.0f} ms, p90 {np.percentile(a, 90) * 1000:.0f} ms, "
            f"bias {errs.mean() * 1000:+.0f} ms | within 40 ms {np.mean(a <= 0.04) * 100:.0f}%, 80 ms {np.mean(a <= 0.08) * 100:.0f}%, 150 ms {np.mean(a <= 0.15) * 100:.0f}%")


def score(files: list, snap: bool):
    from align_engine.pipeline import KaviooAcousticPipeline
    pipe = KaviooAcousticPipeline()
    all_s, all_e = [], []
    for f in files:
        gold = json.loads(Path(f).read_text(encoding="utf-8"))
        audio = Path(gold["audio"])
        if not audio.is_absolute():
            audio = ROOT / audio
        res = pipe.align(str(audio), word_list=[w["w"] for w in gold["words"]], language_code=gold.get("language", "en"), verify_onsets=snap)
        got = res.get("words", [])
        n = min(len(got), len(gold["words"]))
        es = np.array([got[i]["start"] - gold["words"][i]["start"] for i in range(n)])
        ee = np.array([got[i]["end"] - gold["words"][i]["end"] for i in range(n)])
        all_s.append(es)
        all_e.append(ee)
        print(f"{Path(f).name}: {summarize('start', es)}")
        print(f"{'':<{len(Path(f).name)}}  {summarize('end  ', ee)}")
    if len(files) > 1:
        print("\nALL FILES")
        print(summarize("start", np.concatenate(all_s)))
        print(summarize("end  ", np.concatenate(all_e)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("files", nargs="+")
    s.add_argument("--no-snap", action="store_true")
    f = sub.add_parser("from-labels")
    f.add_argument("labels")
    f.add_argument("--audio", required=True)
    f.add_argument("--lang", required=True)
    f.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "score":
        score(a.files, snap=not a.no_snap)
    else:
        from_labels(Path(a.labels), a.audio, a.lang, Path(a.out))


if __name__ == "__main__":
    main()
