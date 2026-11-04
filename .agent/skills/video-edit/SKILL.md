---
name: video-edit
description: End-to-end low-token script-led video editing workflow for Zeno. Use whenever editing raw footage in input/footage/ against a script in input/script/, selecting takes, applying dynamic speaker layouts (single, split_v, split_h, chat_bar, video_call, hybrid), placing right/wrong (✓/✗) marks and SFX, or rendering and verifying final MP4 outputs.
---

# Zeno video edit

Zeno transcribes, measures and renders. You make every editing decision and write `plan.edl`.

Follow `.claude/commands/edit.md` step by step, and `agent/EDIT_RULES.md` for every decision.

**Before choosing clips:** analyse the script and write `work/<project>/script_analysis.md` (emotion, beat, zoom kind, effect sound per line; at most 3 PEAK moments), then plan the zooms and effects from it. Method and tables: `agent/EDIT_RULES.md`.
