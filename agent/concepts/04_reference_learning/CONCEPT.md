# 04 · Reference learning

The user gives **one finished edit plus its script**. Learn the style from it so the same look can be rebuilt from raw footage later.

## Steps
1. **Script** — fetch only the linked tab (`01_script_intake`).
2. **Transcribe and align** the reference audio.
3. **Map** each script line to a start and end time → `styles/<name>/map.json`.
4. **Find layout changes and cuts**, then save a keyframe for each distinct layout plus one contact sheet → `refs/`.
5. **Measure** with OpenCV and Pillow: panel sizes and positions, zoom, borders, fonts, text colours, mark and SFX timing → `spec.json`.
6. **Write** `SKILL.md` and name the folder (`03_style_library`).
7. **Test**: render raw footage with the new style and compare to the reference side by side.

## Never send raw frames to the agent
- Python (OpenCV) reads every frame and writes a small table: face box size (zoom), cut flags, layout per frame.
- The agent reads that table and at most 1–2 contact-sheet images of frames where something changes (cut, layout change, zoom step).
- Looking at individual frames is only for a suspicious measurement: 2–3 frames around it.
- Rough cost: about 5–10k tokens to learn a style, once. Looking at every frame would be millions.

## Tool: `scripts/learn_reference.py`
```bash
python3 scripts/learn_reference.py scan   input/reference/<video>.mp4 --name <style>   # shots, cuts, transitions, face track, contact sheet
python3 scripts/learn_reference.py align  input/reference/<video>.mp4 --name <style> --script input/script/<tab>.txt --lang <lang>
python3 scripts/learn_reference.py frames input/reference/<video>.mp4 --name <style> --start 37.5 --end 93 --step 3 --tag scene3
```
- Output goes to `work/<style>_ref/`. Copy the keepers into `styles/<style>/refs/`.
- `align` splits the audio at scene transitions, so a scene in another language (English scene, Malayalam scene) gets its own transcript.
- Reference videos live in `input/reference/` (not committed).
- Burned-in subtitles in the reference are ignored (`02_footage_intake`).

## Checks that caught mistakes on the first reference
- Whole-frame cut detection missed clip swaps inside split-screen panels. Check each half separately before writing "no cuts".
- A burst of 3+ fast changes within 0.6 s is a transition effect, not several cuts.
- A transcript that starts too early or skips a language is a sign that scene 1 was not transcribed. Check `map.json` match values.

## Model
Use the strongest tier for this step. It runs once per style.
