# 07 · Alignment accuracy

Accuracy of word timing decides whether cuts land cleanly. The aligner is Meta wav2vec2 base (95M, MIT licence), then three energy checks fix its known habits.

## What the Engine does after the model
All in `scripts/align_engine/onset_verifier.py`, applied by `pipeline.py` when `verify_onsets` is on (the default):
1. **Start snap (phrase starts only).** A sentence's first word moves to the nearest real speech onset after a deep energy dip. On the micronarrative reference it fixed two line starts (0.12 s late and 0.22 s early). Snapping every word moved ordinary words by up to 0.34 s, so it is limited to phrase starts.
2. **End extension.** CTC ends words about 60 ms early. Each end is extended while the sound continues, up to 0.25 s, never into the next word.
3. **Real confidence.** Each word has a 0-1 score. English words score about 0.8, Malayalam about 0.1, because the model hears Indic words through English-style letters. Compare words within one language.

## Measured so far (synthetic isolated-word test set, 25 words, 5 languages)
| | median error | within 80 ms |
|---|---|---|
| Start, CTC only | 48 ms | 72% |
| Start, with checks | 10 ms | 92% |
| End, CTC only | 62 ms | 72% |
| End, with checks | 10 ms | 96% |

This set uses energy-defined boundaries and clean pauses, so it flatters the energy checks. **Real footage is not measured yet.**

## Measure on real footage (do this before changing the model)
1. Open a real clip in Audacity, add a label at each word's start and end (about 100–200 words across the languages and voices you use), and export the labels.
2. `python scripts/eval_alignment.py from-labels labels.txt --audio gold/audio/x.wav --lang ml --out gold/x.json`
3. `python scripts/eval_alignment.py score gold/*.json` (add `--no-snap` to see what the checks add).
Keep gold JSON in `gold/`. Audio goes on the shared drive, not git.

## When to fine-tune the model
Only if scores on real footage stay poor after the checks (for example, median error above 60 ms or under 80% within 80 ms), and only with a held-out gold set. Needs hours of audio paired with checked transcripts, an NVIDIA GPU, and the `transformers` package. Save each job's audio and checked transcript to a shared folder as training data from now on; `scripts/clean.py` deletes old audio in `work/`, so do not store it there.

## Not done
- Hindi, Marathi, Gujarati and Bengali words get an unspoken final "a" in the romanised form. Test a fix on real Hindi footage before applying it.
- Mid-sentence word boundaries are not energy-checked (too many false dips).
