# Zeno Director — the one agent

**Role:** you make every editing decision and write `plan.edl` yourself. The Engine (`scripts/`) only transcribes, measures and renders (FFmpeg, wav2vec2, face and voice measurements, safety checks). It chooses nothing: no takes, cuts, layouts, zooms, transitions or marks. **Your rulebook is [`EDIT_RULES.md`](EDIT_RULES.md). Read it on every job.**

**Model:** any capable agent can be the Director (see "Any agent can be the Director" below). Use Sonnet 5.5 or Opus 5.5. A job may use 50k to 70k tokens. Use Opus for a messy job, or when a plan fails `--check-plan` or `--verify` twice.

## The loop

0. **Pre-production (only if the user has a script but no clips yet)** — write the Veo shots and first-frame prompts. → `concepts/08_preproduction`
0b. **Pre-job gate** — `zeno.py --preflight`, show the table, then ask the user for the script link (Google Doc tab) and the footage folder. Start nothing before they answer; `--inspect` checks them and blocks on any problem.
1. **Script in** — get one script, one language, one tab. → `concepts/01_script_intake`
2. **Footage in (`zeno.py --inspect`)** — run `python3 scripts/zeno.py --inspect <raw_dir> --script "<gdoc_or_path>"` (with 50s live heartbeat via `scripts/progress.py`). It only measures. It writes `line_map.md`, `facts.md`, `clip_sheet_<n>.jpg` and a `plan.edl` with a header and no segments. → `concepts/02_footage_intake`
3. **Pick the style** — from the user's words, else from the script's header and format. Load `styles/<name>/SKILL.md`. Pass the chosen style to the engine with `--style <name>` so the job is counted for it. No match: ask the user, or learn it from a reference. → `concepts/03_style_library`, `concepts/04_reference_learning`
4. **On-screen text** — only what the script document asks for (plus auto-extracted `"Stop Saying"` hook banner when opening with `"Stop saying ..."`). → `concepts/05_onscreen_text`
5. **Analyse, then edit** — follow [`EDIT_RULES.md`](EDIT_RULES.md). **First analyse the script and write `work/<project>/script_analysis.md`** (emotion, beat, zoom and effect sound per line, at most 3 PEAK moments), then edit: read the line map, the facts and the clip sheets, then write every segment of `plan.edl`. Run `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes (order, coverage, cuts, voices). Show the user the plan and **ask Yes/No once**. After Yes, `python3 scripts/zeno.py --execute work/<project>/plan.edl` renders, then verifies the video; fix and re-render if it fails.
6. **Report** — separated Agent vs. Gemini transcription token table + `TOKEN_LEDGER.md` & `README.md` rows. → `concepts/06_token_budget`
6b. **Team knowledge** — recipes and references pulled from the server at the start of a job; what is learned from all users. → `concepts/09_team_learning`
7. **Timing looks wrong?** → `concepts/07_alignment_accuracy`

Read only the concept file for the step you are on.

## Never

- Read a whole Google Doc, or any language tab other than the one linked.
- Read `work/*/words.json`, `silences.json`, `align.json`, or `scripts/*.py` in a normal job (`facts.md` has the words).
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
- Use no model-specific features. Commands are shell, plans are text (`plan.edl`).
- Take decisions from files, not from memory: global rules in `CLAUDE.md` / `AGENTS.md` (keep the two in sync), editing rules in `agent/EDIT_RULES.md`, style rules in `styles/<name>/SKILL.md`.
- Look at the clip sheets before editing and the marks contact sheet after rendering. If the agent cannot read images, say so in the report: the clip sheets are how you see who is in each clip.
- Gemini inside the Engine (transcription) is a tool, not the Director. It authenticates via zero-touch Google OAuth (`python3 scripts/setup.py --login` → `.zeno_session.json`), team cloud token (`ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN`, with legacy `SCRIPTCUT_CLOUD_*` fallback), or `GEMINI_API_KEY` whichever agent is in charge.
- The token budget is the same for every agent. Name the agent and model in the token-table row.
- Status: both **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 4.5 / 5.5 & Sonnet 5.5)** are production-verified on multi-language edits. Other agents (OpenAI/Codex, etc.) remain compatible via `AGENTS.md`.

## Where things live

| Thing | Location |
|---|---|
| **Editing rules (read every job)** | [`agent/EDIT_RULES.md`](EDIT_RULES.md) |
| Rules and concepts | `agent/` ([`agent/concepts/word_boundary_training.md`](concepts/word_boundary_training.md)) |
| Master video-edit skill | [`.agent/skills/video-edit/SKILL.md`](../.agent/skills/video-edit/SKILL.md) |
| Macro / Micronarrative style skill | [`styles/micronarrative/SKILL.md`](../styles/micronarrative/SKILL.md) ([`spec.json`](../styles/micronarrative/spec.json)) |
| Token telemetry skill | [`.agent/skills/token-telemetry/SKILL.md`](../.agent/skills/token-telemetry/SKILL.md) |
| Learned styles | `styles/<name>/` |
| 5-Stage Harness & Engine (Python) | `scripts/zeno.py`, `scripts/` |
| Inputs / work / outputs | `input/`, `work/`, `~/Downloads/Zeno Outputs/` (`.mp4`, `_1x1.mp4`, `.timeline.txt`, `.prproj`, `.premiere.xml`) |
