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
- `align` transcribes the whole audio in one pass. English and native words are handled word by word (`02_footage_intake`, "Mixed languages").
- Reference videos live in `input/reference/` (not committed).
- Burned-in subtitles in the reference are ignored (`02_footage_intake`).

## Proving the style without raw footage
When the raw clips are not available, cut stand-in clips from the reference and rebuild it:
```bash
python3 scripts/learn_reference.py standins input/reference/<video>.mp4 --name <style>      # cuts into input/standins/<style>/
python3 scripts/style_plan.py <style> input/standins/<style>/manifest.json --out work/<style>_rebuild/plan.json --canvas 720x1280@24
python3 scripts/render.py work/<style>_rebuild/plan.json --draft
```
Then compare frame by frame with the reference. Clean frames should differ by only a few levels (of 255). This proves the render side only, not take selection on real footage. Flash-covered frames are replaced by holding the nearest clean frame (`STANDIN_PIECES` is specific to the micronarrative reference).

## Checks that caught mistakes on the first reference
- Whole-frame cut detection missed clip swaps inside split-screen panels. Check each half separately before writing "no cuts".
- A burst of 3+ fast changes within 0.6 s is a transition effect, not several cuts.
- A transcript that starts too early or skips a language is a sign that scene 1 was not transcribed. Check `map.json` match values.

## Model
Use the strongest tier for this step. It runs once per style.

## Keep knowledge, not footage (format library)
Reference and sample footage is never committed and no model reads its frames. Measure it locally with OpenCV and keep only the numbers:
```bash
python3 scripts/format_library.py extract <video> --id split_h --layout split_h --start 37 --end 93 --names "vertical split screen,side by side" --description "..." [--thumbs]
python3 scripts/format_library.py show split_h        # about 100 tokens: what an agent reads
```
The recipe in `styles/formats/<id>.json` records the seam position, faces per frame, face size, eye line, headroom and motion, with `confidence` `measured` or `defined` (a layout rule not yet measured on a reference). The script directs the edit: `prepare.py` looks up the scene wording in this library (a "split" in the wording still wins) to choose the layout. Recipe numbers do not yet drive the renderer; `layouts.py` still owns the geometry.

## Learning from every job (team format library)
When `render.py` finishes, `format_learning.py` sends the numbers for each format the plan used (shots, seconds, zoom-in share, transitions per minute, marks, strike-through banners) to the Zeno server: numbers only, never the script text (a hash identifies the script), footage or video. Add `# style: <name>` to `plan.edl` so a job is counted for its style. The server counts a job once per script (the newest render wins), only if it passed `--verify`, and shows candidates in `GET /admin/api/formats`. An admin approves a candidate (`POST /admin/api/formats/<style>/<format>/approve`, needs at least `ZENO_FORMAT_MIN_JOBS`, default 5, different scripts). `python3 scripts/zeno.py --sync-learnings` copies approved recipes to `styles/formats/team/` (they carry no aliases, so they never change a layout choice by themselves).
