---
name: video-edit
description: End-to-end low-token script-led video editing workflow for Zeno. Use whenever editing raw footage in input/footage/ against a script in input/script/, selecting takes, applying dynamic speaker layouts (single, split_v, split_h, chat_bar, video_call, hybrid), placing right/wrong (✓/✗) marks and SFX, or rendering and verifying final MP4 outputs.
---

# Zeno video edit

Zeno transcribes, measures and renders. You make every editing decision and write `plan.edl`.

Follow `.claude/commands/zeno.md` step by step, and `agent/EDIT_RULES.md` for every decision.

**Notion Naming & Output Folder Rule:** Never create output folders with random or invented names. Always ask the user for the **Notion name** (along with the script link and footage folder if not already given) and pass `--name "<Notion name>"` to `zeno.py --inspect` / `prepare.py`. Zeno creates `~/Downloads/Zeno Outputs/<Notion name>/` with `"reel"` before the 9:16 reel video (`reel_<Notion name>.mp4`) and `"SQ"` before the 1:1 square video (`SQ_<Notion name>.mp4`).

**Before choosing clips:** analyse the script and write `work/<project>/script_analysis.md` (emotion, beat, zoom kind, effect sound per line; at most 3 PEAK moments), then plan the zooms and effects from it. Method and tables: `agent/EDIT_RULES.md`.
