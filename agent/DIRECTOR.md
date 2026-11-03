# Zeno Director — the one agent

**Role:** decision-maker only. The Engine (`scripts/`) does everything measurable: transcription, alignment, face tracking, rendering, QA. You read small files, decide, and make tiny edits to `plan.edl`.

**Model:** any capable agent can be the Director (see "Any agent can be the Director" below). Use the cheaper tier by default (for example Sonnet 5.5). Use the strongest tier (for example Opus 5.5) only to learn a new style from a reference video, or when a plan fails `--verify` twice.

## The loop

0. **Pre-production (only if the user has a script but no clips yet)** — write the Veo shots and first-frame prompts. → `concepts/08_preproduction`
1. **Script in** — get one script, one language, one tab. → `concepts/01_script_intake`
2. **Footage in & Stage 1–3 Pitch (`zeno.py --inspect`)** — run `python3 scripts/zeno.py --inspect <raw_dir> --script "<gdoc_or_path>"` (with 50s live heartbeat via `scripts/progress.py`), read `brief.txt`, `plan.edl`, and `pitch_card.md` only. → `concepts/02_footage_intake`
3. **Pick the style** — from the user's words, else from the script's header and format. Load `styles/<name>/SKILL.md`. No match: ask the user, or learn it from a reference. → `concepts/03_style_library`, `concepts/04_reference_learning`
4. **On-screen text** — only what the script document asks for (plus auto-extracted `"Stop Saying"` hook banner when opening with `"Stop saying ..."`). → `concepts/05_onscreen_text`
5. **Edit & Pre-Render Gate (5-Agent Parallel Workflow)** — refine `plan.edl` using Agents 1–4 (`1: Script/Take Auditor`, `2: Pause-Cut & De-Duplication Specialist`, `3: Dynamic Zoom & Layout Choreographer`, `4: Transitions, Strobe Overlays & SFX Designer`), refresh `python3 scripts/zeno.py --pitch work/<project>/plan.edl`, present the **Editorial Pitch Card**, and **ask the user Yes/No** before rendering. After approval, run `python3 scripts/zeno.py --execute work/<project>/plan.edl` (45ms zero-drift audio crossfades, `-48 dBFS` room-tone floor, per-clip RMS gain matching, transcript-intent zooms, `.mp4` + `_1x1.mp4` + `.prproj` + `.premiere.xml`) → **Agent 5** performs local Python deep post-render inspection (verifying 0 repeated dialogues, 0 empty silences `>1.05s`, 0 boundary clicks, `-11.5 LUFS` / `-1.0 dBTP`, active transitions/whoosh SFX, and 2-pulse Red/Green overlays) and auto-fixes if needed.
6. **Report** — separated Agent vs. Gemini transcription token table + `TOKEN_LEDGER.md` & `README.md` rows. → `concepts/06_token_budget`
7. **Timing looks wrong?** → `concepts/07_alignment_accuracy`

Read only the concept file for the step you are on.

## Never

- Read a whole Google Doc, or any language tab other than the one linked.
- Read `work/*/words.json`, `silences.json`, `align.json`, or `scripts/*.py` in a normal job.
- Analyze or copy burned-in subtitles in client footage (for now).
- Add captions, graphics or B-roll the script or style did not ask for.
- Build an Engine feature from assumptions. Measure a reference first.

## Any agent can be the Director

The repo is the interface, not a model. The Director only reads plain-text files, runs shell commands, and makes small edits to `plan.edl`. Any agent that can do that can run a job.

| Agent | Entry file |
|---|---|
| Claude Code (Opus, Sonnet) | `CLAUDE.md` → this file |
| Antigravity (Gemini 4 Argon) | `AGENTS.md` and `.agent/` → this file |
| OpenAI and other agents | `AGENTS.md` → this file |

To stay portable:
- Use no model-specific features. Commands are shell, plans are text (`brief.txt`, `plan.edl`).
- Take decisions from files, not from memory: global rules in `CLAUDE.md` / `AGENTS.md` (keep the two in sync), style rules in `styles/<name>/SKILL.md`.
- One image read per job (the marks contact sheet). If the agent cannot read images, skip it, rely on the `--verify` output, and say so in the report.
- Gemini inside the Engine (transcription) is a tool, not the Director. It authenticates via zero-touch Google OAuth (`python3 scripts/setup.py --login` → `.zeno_session.json`), team cloud token (`ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN`, with legacy `SCRIPTCUT_CLOUD_*` fallback), or `GEMINI_API_KEY` whichever agent is in charge.
- The token budget is the same for every agent. Name the agent and model in the token-table row.
- Status: both **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 4.5 / 5.5 & Sonnet 5.5)** are production-verified on multi-language edits. Other agents (OpenAI/Codex, etc.) remain compatible via `AGENTS.md`.

## Where things live

| Thing | Location |
|---|---|
| Rules and concepts | `agent/` ([`agent/concepts/word_boundary_training.md`](concepts/word_boundary_training.md)) |
| Master video-edit skill | [`.agent/skills/video-edit/SKILL.md`](../.agent/skills/video-edit/SKILL.md) |
| Macro / Micronarrative style skill | [`styles/micronarrative/SKILL.md`](../styles/micronarrative/SKILL.md) ([`spec.json`](../styles/micronarrative/spec.json)) |
| Token telemetry skill | [`.agent/skills/token-telemetry/SKILL.md`](../.agent/skills/token-telemetry/SKILL.md) |
| Learned styles | `styles/<name>/` |
| 5-Stage Harness & Engine (Python) | `scripts/zeno.py`, `scripts/` |
| Inputs / work / outputs | `input/`, `work/`, `~/Downloads/Zeno Outputs/` (`.mp4`, `_1x1.mp4`, `.timeline.txt`, `.prproj`, `.premiere.xml`) |
