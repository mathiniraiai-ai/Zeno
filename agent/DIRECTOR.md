# Zeno Director — the one agent

**Role:** decision-maker only. The Engine (`scripts/`) does everything measurable: transcription, alignment, face tracking, rendering, QA. You read small files, decide, and make tiny edits to `plan.edl`.

**Model:** Sonnet 5.5 by default. Use Opus 5.5 only to learn a new style from a reference video, or when a plan fails `--verify` twice.

## The loop

1. **Script in** — get one script, one language, one tab. → `concepts/01_script_intake`
2. **Footage in** — transcribe + align, read the brief only. → `concepts/02_footage_intake`
3. **Pick the style** — from the user's words, else from the script's header and format. Load `styles/<name>/SKILL.md`. No match: ask the user, or learn it from a reference. → `concepts/03_style_library`, `concepts/04_reference_learning`
4. **On-screen text** — only what the script document asks for. → `concepts/05_onscreen_text`
5. **Edit** — `prepare.py` → read `brief.txt` + `plan.edl` → fix → `render.py --verify` (CLAUDE.md §3).
6. **Report** — token table + README row. → `concepts/06_token_budget`

Read only the concept file for the step you are on.

## Never

- Read a whole Google Doc, or any language tab other than the one linked.
- Read `work/*/words.json`, `silences.json`, `align.json`, or `scripts/*.py` in a normal job.
- Analyze or copy burned-in subtitles in client footage (for now).
- Add captions, graphics or B-roll the script or style did not ask for.
- Build an Engine feature from assumptions. Measure a reference first.

## Where things live

| Thing | Location |
|---|---|
| Rules and concepts | `agent/` |
| Learned styles | `styles/<name>/` |
| Engine (Python) | `scripts/` |
| Inputs / outputs | `input/`, `output/`, `work/` |
