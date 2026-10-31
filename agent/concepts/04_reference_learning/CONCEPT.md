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

## Status
- `scripts/learn_reference.py` does **not exist yet**. It is built when the first reference arrives, from what that reference actually needs.
- Ignore burned-in subtitles in the reference too (`02_footage_intake`).
- Use Opus 5.5 for this step. It runs once per style.
