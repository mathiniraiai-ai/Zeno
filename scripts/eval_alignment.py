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


def _ensure_default_gold_set() -> list[Path]:
    """Return existing gold JSONs in gold/ or work/gold_synth/, or synthesize a deterministic benchmark."""
    for candidate_dir in (ROOT / "gold", ROOT / "work" / "gold_synth"):
        if candidate_dir.is_dir():
            jsons = []
            for p in sorted(candidate_dir.glob("*.json")):
                try:
                    aud = json.loads(p.read_text(encoding="utf-8")).get("audio", "")
                    if (ROOT / aud).is_file() or Path(aud).is_file():
                        jsons.append(p)
                except Exception:
                    pass
            if jsons:
                return jsons

    import wave
    synth_dir = ROOT / "work" / "gold_synth"
    synth_dir.mkdir(parents=True, exist_ok=True)
    wav_out = synth_dir / "synth_en.wav"
    json_out = synth_dir / "synth_en.json"
    sr = 16000
    specs = [("hello", 0.30, 0.72), ("world", 0.95, 1.42), ("stop", 1.68, 2.08), ("saying", 2.30, 2.82)]
    dur = 3.20
    t = np.arange(int(dur * sr), dtype=np.float32) / sr
    sig = np.zeros_like(t)
    for idx, (_, ws, we) in enumerate(specs):
        mask = (t >= ws) & (t <= we)
        wt = t[mask] - ws
        env = np.sin(np.pi * wt / max(we - ws, 1e-3)) ** 0.6
        f0 = 135.0 + 15.0 * idx
        burst = (
            0.35 * np.sin(2 * np.pi * f0 * wt)
            + 0.20 * np.sin(2 * np.pi * 2.0 * f0 * wt)
            + 0.10 * np.sin(2 * np.pi * 5.0 * f0 * wt)
        )
        sig[mask] = (env * burst).astype(np.float32)
    pcm = np.clip(sig * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(wav_out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    payload = {
        "audio": str(wav_out.relative_to(ROOT)),
        "language": "en",
        "words": [{"w": w, "start": s, "end": e} for w, s, e in specs],
        "note": "self-contained deterministic synthetic benchmark",
    }
    json_out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [json_out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="Run self-contained gold-set alignment benchmark")
    sub = ap.add_subparsers(dest="cmd", required=False)
    s = sub.add_parser("score")
    s.add_argument("files", nargs="*")
    s.add_argument("--no-snap", action="store_true")
    f = sub.add_parser("from-labels")
    f.add_argument("labels")
    f.add_argument("--audio", required=True)
    f.add_argument("--lang", required=True)
    f.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.self_test or a.cmd is None:
        files = _ensure_default_gold_set()
        score(files, snap=True)
    elif a.cmd == "score":
        files = a.files or _ensure_default_gold_set()
        score(files, snap=not a.no_snap)
    else:
        from_labels(Path(a.labels), a.audio, a.lang, Path(a.out))


if __name__ == "__main__":
    main()
