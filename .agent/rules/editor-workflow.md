---
trigger: always_on
description: Low-token 3-step video editing workflow, hard boundaries, layout presets, and mandatory token reporting for Zeno.
---

# Zeno — Always-On Workspace Rule (Antigravity / Gemini 4 Argon)

## 1. Hard Rules
- **Workspace Isolation**: Never modify any files outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never touch other projects in `Documents`.
- **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
- **Roles**: Zeno transcribes, measures and renders. The agent makes every editing decision and writes `plan.edl`. Read `agent/DIRECTOR.md` and `agent/EDIT_RULES.md`.
- **Script is the timeline**: file names and file order mean nothing. Choose takes from `line_map.md` and `facts.md`, and look at the clip sheets.
- **Check before Yes/No**: `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` must pass, then ask Yes/No once, then `--execute`.
- **Budget**: 50k to 70k tokens per video.
