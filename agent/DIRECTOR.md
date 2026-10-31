# Zeno Director — the one agent

**Role:** decision-maker only. The Engine (`scripts/`) does everything measurable: transcription, alignment, face tracking, rendering, QA. You read small files, decide, and make tiny edits to `plan.edl`.

**Model:** any capable agent can be the Director (see "Any agent can be the Director" below). Use the cheaper tier by default (for example Sonnet 5.5). Use the strongest tier (for example Opus 5.5) only to learn a new style from a reference video, or when a plan fails `--verify` twice.

## The loop

0. **Pre-production (only if the user has a script but no clips yet)** — write the Veo shots and first-frame prompts. → `concepts/08_preproduction`
1. **Script in** — get one script, one language, one tab. → `concepts/01_script_intake`
2. **Footage in** — transcribe + align, read the brief only. → `concepts/02_footage_intake`
3. **Pick the style** — from the user's words, else from the script's header and format. Load `styles/<name>/SKILL.md`. No match: ask the user, or learn it from a reference. → `concepts/03_style_library`, `concepts/04_reference_learning`
4. **On-screen text** — only what the script document asks for. → `concepts/05_onscreen_text`
5. **Edit** — `prepare.py` → read `brief.txt` + `plan.edl` → fix → `render.py --verify` (CLAUDE.md §3).
6. **Report** — token table + README row. → `concepts/06_token_budget`
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
| Antigravity (Gemini) | `AGENTS.md` and `.agent/` → this file |
| OpenAI and other agents | `AGENTS.md` → this file |

To stay portable:
- Use no model-specific features. Commands are shell, plans are text (`brief.txt`, `plan.edl`).
- Take decisions from files, not from memory: global rules in `CLAUDE.md` / `AGENTS.md` (keep the two in sync), style rules in `styles/<name>/SKILL.md`.
- One image read per job (the marks contact sheet). If the agent cannot read images, skip it, rely on the `--verify` output, and say so in the report.
- Gemini inside the Engine (transcription) is a tool, not the Director. It needs `GEMINI_API_KEY` whichever agent is in charge.
- The token budget is the same for every agent. Name the agent and model in the token-table row.
- Status: only Claude has run this repo so far. Other agents are untested; the first job with one is a trial run.

## Where things live

| Thing | Location |
|---|---|
| Rules and concepts | `agent/` |
| Learned styles | `styles/<name>/` |
| Engine (Python) | `scripts/` |
| Inputs / outputs | `input/`, `output/`, `work/` |
