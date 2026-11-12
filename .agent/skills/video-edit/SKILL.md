---
name: video-edit
description: End-to-end low-token script-led video editing workflow for Zeno. Use whenever editing raw footage in input/footage/ against a script in input/script/, selecting takes, applying dynamic speaker layouts (single, split_v, split_h, chat_bar, video_call, hybrid), placing right/wrong (✓/✗) marks and SFX, or rendering and verifying final MP4 outputs.
---

# Zeno video edit

Zeno transcribes, measures and renders. You make every editing decision and write `plan.edl`.

Follow `.claude/commands/zeno.md` step by step, and `agent/EDIT_RULES.md` for every decision.

**Python Interpreter & First-Time Setup:** Never run bare `python` or `python3` (on Windows it hits `WindowsApps\python.exe` with `Exit code 49`). Always invoke `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python` (macOS/Linux). If `.venv` is missing, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_windows.ps1` (Windows) or `bash install.command` (macOS/Linux) once first.

**Notion Naming & Output Folder Rule:** Never create output folders with random or invented names. Always ask the user for the **Notion name** (along with the script link and footage folder if not already given) and pass `--name "<Notion name>"` to `zeno.py --inspect` / `prepare.py`. Zeno creates `~/Downloads/Zeno Outputs/<Notion name>/` with `"reel"` before the 9:16 reel video (`reel_<Notion name>.mp4`) and `"SQ"` before the 1:1 square video (`SQ_<Notion name>.mp4`).

**Before choosing clips:** analyse the script and write `work/<project>/script_analysis.md` (emotion, beat, active-panel or full-frame zoom kind, calibrated `-4 to 0 dB` SFX per line; ~1 PEAK per `12–18s`), then write `plan.edl` with `#subs auto y=72` ON by default (`y=50` automatically on `split_v` seam; `#subs off` only if the user explicitly requests no subtitles), trimming all `! REPEAT / STUMBLE` flags in `facts.md`, cutting `> 0.25s` pauses with `1.08` punch-ins, and placing smooth eased transitions every `7–9s`. Method and tables: `agent/EDIT_RULES.md`.
