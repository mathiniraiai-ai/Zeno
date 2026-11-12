---
name: video-edit
description: End-to-end low-token script-led video editing workflow for Zeno. Use whenever editing raw footage in input/footage/ against a script in input/script/, selecting takes, applying dynamic speaker layouts (single, split_v, split_h, chat_bar, video_call, hybrid), placing right/wrong (✓/✗) marks and SFX, or rendering and verifying final MP4 outputs.
---

# Zeno video edit

**Split screens never zoom (owner's rule, overrides any older wording in this file).** Every `split_h`, `split_v` and baked-split segment is `z=1`: no push-in, punch-in, step zoom or pull-out. The renderer ignores a zoom written on a split and holds each panel in one fixed frame (14% headroom, the speaking and the silent clip of a character lined up by their background), so switching between them never jumps.

**Captions (owner's rule, overrides any older wording in this file).** `#subs auto y=68` is the default. One style is used for the whole video, at one fixed size (54 px on a 1080-wide picture; `size=` changes it), always on a single row (never two rows), with no per-caption colour changes unless the plan says `adapt=on`. On full-frame cuts captions sit at 65 to 70% of the height (`y=68`). In a top/bottom split they always sit on the divider line in the middle (`y=50`), whatever `y=` says, so neither face is covered.

**Job archive and feedback (owner's rule).** Every edit is stored end to end on the Zeno server by itself (script, every version of the plan, what each check said, verification, token numbers): nothing to do for that. What only you can add is the human's words: whenever the user comments on the plan or the finished video, or asks for a change ("the head is cut in L4", "subtitle too big", "this one is perfect"), first run `<PY> scripts/zeno.py --feedback "<their words, as said>" --plan work/<project>/plan.edl`, then act on it. One call per comment; it takes a second and never blocks the job.


Zeno transcribes, measures and renders. You make every editing decision and write `plan.edl`.

Follow `.claude/commands/zeno.md` step by step, and `agent/EDIT_RULES.md` for every decision.

**Python Interpreter & First-Time Setup:** Never run bare `python` or `python3` (on Windows it hits `WindowsApps\python.exe` with `Exit code 49`). Always invoke `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python` (macOS/Linux). If `.venv` is missing, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_windows.ps1` (Windows) or `bash install.command` (macOS/Linux) once first.

**Notion Naming & Output Folder Rule:** Never create output folders with random or invented names. Always ask the user for the **Notion name** (along with the script link and footage folder if not already given) and pass `--name "<Notion name>"` to `zeno.py --inspect` / `prepare.py`. Zeno creates `~/Downloads/Zeno Outputs/<Notion name>/` with `"reel"` before the 9:16 reel video (`reel_<Notion name>.mp4`) and `"SQ"` before the 1:1 square video (`SQ_<Notion name>.mp4`).

**Before choosing clips:** analyse the script and write `work/<project>/script_analysis.md` (emotion, beat, active-panel or full-frame zoom kind, calibrated `-4 to 0 dB` SFX per line; ~1 PEAK per `12–18s`), then write `plan.edl` with `#subs auto y=68` ON by default (`y=50` automatically on `split_v` seam; `#subs off` only if the user explicitly requests no subtitles), trimming all `! REPEAT / STUMBLE` flags in `facts.md`, cutting `> 0.25s` pauses with `1.08` punch-ins, and placing smooth eased transitions every `7–9s`. Method and tables: `agent/EDIT_RULES.md`.
