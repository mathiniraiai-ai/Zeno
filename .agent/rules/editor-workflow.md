---
trigger: always_on
description: Low-token 3-step video editing workflow, hard boundaries, layout presets, and mandatory token reporting for Zeno.
---

# Zeno — Always-On Workspace Rule (Antigravity / Gemini 4 Argon)

## 1. Hard Rules
- **Workspace Isolation**: Never modify any files outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never touch other projects in `Documents`.
- **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
- **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
- **Speakers**: The script names who speaks each line (`L2 Robot: ...`). `prepare.py` measures the voices and cuts any line spoken by the wrong character (`[WRONG_SPEAKER]` in `brief.txt`), then looks in every clip, including unused takes, for that line said by the right character and splices it in (`[FILL]`). `[WRONG_SPEAKER_NO_FIX]` means no clip has the right voice saying it: tell the user to reshoot. `[SPEAKER_CHECK] not applied` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
- **Token Conservation**: Never read `work/S*/words.json`, `work/S*/silences.json`, or `scripts/*.py` unless debugging a specific word boundary or script exception.
- **Fresh Sessions**: Always run edits in a fresh session per video with unused MCP servers disabled.

## 2. Token Budget & Mandatory Reporting
- **Target**: `12k–18k tokens` per minute of finished video (**strict ceiling: `20k tokens / min`**; under `20k–30k total` for a typical 1.5-min edit).
- **Mid-Job Warning**: Warn the user immediately if messy footage or excessive retakes risk exceeding the `20k tokens / min` ceiling.
- **Mandatory End-of-Job Output**: Print the Token Usage table at the end of every job and append the new row to `README.md`:
  `| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |`

## 3. The 5-Stage Interactive Zeno Harness (`scripts/zeno.py`)
0. **Stage 0 — Readiness Check**: `python3 scripts/zeno.py --check` (`[✓] Installed` vs `[✗] Remaining`).
1. **Stages 1–3 — Ingest, Transcribe, Match & Pitch (`1 command` + 50s Live Heartbeat)**:
   `python3 scripts/zeno.py --inspect <raw_dir_or_clips> --script <script_or_gdoc_url> [--lang <lang>] [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] [--mark-pack default|glossy|flash|vignette]`
   Schedule a 50s timer while running in the background and read `python3 scripts/progress.py` (`work/<project>/progress.json`) to post live stage updates in chat.
2. **Stage 3 Gate — Refine Plan & Ask Yes/No (`Mandatory Stop`)**:
   Read ONLY `work/<project>/brief.txt`, `work/<project>/plan.edl`, and `work/<project>/pitch_card.md`. Refine `plan.edl` using Agents 1–4, refresh the pitch card (`python3 scripts/zeno.py --pitch work/<project>/plan.edl`), present the **Editorial Pitch Card**, and **ask the user Yes/No** before rendering.
3. **Stages 4–5 — Render, Premiere `.prproj` Export & Deep QC (`1 command` after Yes)**:
   `python3 scripts/zeno.py --execute work/<project>/plan.edl [--square]`
   Renders the final MP4 + `_1x1.mp4` + `.timeline.txt` + editable Adobe Premiere Pro project files (`.prproj` + `.premiere.xml`) to `~/Downloads/Zeno Outputs/`, runs deterministic script coverage & `-11.5 LUFS` loudness verification, and generates `work/<project>/marks_contact_sheet.jpg` (`1 image read` to verify `✓`/`✗` marks).

## 4. Dynamic Layouts, Pre-Stitched Split Screens, 2-Pulse Overlays & 5-Agent Workflow
- **Layouts**: `single` (full-frame + dynamic zoom-in/zoom-out, **AND straight matching with `zoom=1.0` for Google Flow pre-stitched vertical split-screen clips**), `split_v` (top/bottom 50/50 split for separate character files), `split_h` (left/right 50/50 split for separate character files), `chat_bar` (speaker + floating prompt bar), `video_call` (speaker + rounded PiP card), and `hybrid` (`auto` mix of `single` for monologues and split/PiP/chat_bar for dialogues).
- **Mark Packs**: `flash` (**default** — full-frame `red_flash.png` / `green_flash.png` at `0.50` alpha triggering **only at the end of the wrong/right sentence** and **flashing 2 times (`0.56s`: Pulse 1 → OFF → Pulse 2)** + `+4 dB` error buzzer & `+2.5 dB` right ding), `vignette` (`w42_red_vignette.png` / `w42_green_vignette.png` 2-pulse at sentence end), `default`, `glossy`, or custom PNGs inside `assets/marks/<pack>/`.
- **Skill Links**: [`.agent/skills/video-edit/SKILL.md`](../skills/video-edit/SKILL.md) | [`styles/micronarrative/SKILL.md`](../../styles/micronarrative/SKILL.md) | [`agent/DIRECTOR.md`](../../agent/DIRECTOR.md) | [`agent/concepts/word_boundary_training.md`](../../agent/concepts/word_boundary_training.md)
- **5-Agent Parallel Workflow & Agent 5 Deep Analyzer**: Assign at least 5 agents per task (defaulting to Antigravity or Claude Code). **Agent 5** is the mandatory **Post-Render Deep Video Analyzer & Auto-Fixer**: once the output MP4 is generated, Agent 5 analyzes the complete video locally using Python (`verify.py`, `words.json` timestamps, FFmpeg `silencedetect`, WAV RMS energy, and RGB frame inspection) to verify zero repeated dialogues, zero empty silences (`>1.05s`), active transitions + Zero-Audio-Loss splices, and 2-pulse end-of-sentence Red/Green PNG overlays, fixing and re-rendering automatically if any issue is found. **Thermal Guardrail**: Parallel subagents (`Agent 1`–`Agent 4`) must operate strictly on text files (`brief.txt`, `plan.edl`) and never run concurrent Python/OpenCV/FFmpeg processes over raw video files.

## Secrets (all agents)
Never open, print, quote or paste `.env` or any key in a chat or log. It holds the user's personal Zeno key (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke`; it reports [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
