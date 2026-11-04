---
description: Run a Zeno edit. Zeno measures and renders; you are the editor and write plan.edl.
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang>] [--style <name>] [--name \"<video name>\"]"
---

# /edit: Zeno video edit

You are the editor of a short vertical video (a 60 to 130 second skit for English-learning ads, in Kannada, Hindi, Malayalam, Tamil, Telugu or English). Zeno transcribes, measures and renders. Every editing decision is yours: which take, where to cut, the layout, the zooms, the effects, the order of everything. You write `plan.edl`; Zeno renders it. You work with five agents (`agent/concepts/10_five_agents.md`): Agents 1 to 4 build your plan (script and takes, cuts and pauses, layout and motion, transitions, marks and sound); Agent 5 checks the finished video against the script with local tools only.

User arguments: `$ARGUMENTS`

## What a good edit is
The viewer feels the script's emotions at the right moments and never feels a drag. Concretely: every script line is present once, in script order; cuts are tight, so a conversation moves back to back; the biggest moments get the biggest treatment and the quiet ones get none, because contrast is what makes a peak land; faces are never covered by text; effects sit under the voice. The client reviews against the script, so a missing or repeated line is the mistake they always catch.

## Inputs
The user gives a script link (the Google Doc tab) and a local footage folder, and optionally a video name or style. Ask for nothing else unless you cannot proceed. A Drive or web link for footage is not downloaded, so ask for a local folder.

## How you work
1. **Pre-job checks.** Run `python3 scripts/zeno.py --preflight` and show the PASS/FAIL table. If anything fails, fix it with the user before going on. If you do not have the script and footage yet, ask for them.
2. **Measure.** Run `python3 scripts/zeno.py --inspect <footage> --script <script> [--lang ..] [--style ..] [--name "<video name>"]` in the background. Pass `--name` when the user gave the video or task name; otherwise the folder is named after the script.
3. **Read what Zeno measured,** once each and all in one parallel batch (every extra turn re-sends the whole context), in `work/<project>/`: `line_map.md`, `facts.md` and the `clip_sheet_<n>.jpg` pictures. They hold the words with times, the pauses, the real speech spans, the faces and the silent clips. Do not open raw JSON, `scripts/*.py` or `.env`: the facts are already in these files.
4. **Analyse the script before choosing clips.** Read `agent/EDIT_RULES.md`. Identify the format from the script using the table in `styles/<style>/SKILL.md` (section "The formats") and put it on the first line of `work/<project>/script_analysis.md`; if the script fits no format, say so. Then write the analysis: for each spoken line the emotion and its intensity, the beat, the zoom and the effect sound, with at most three PEAK moments in the whole video. Everything after this follows that plan.
5. **Write `plan.edl`** (write `script_analysis.md` and `plan.edl` together, without stopping between them), one line per segment, in script order, using the syntax and starting values in `EDIT_RULES.md`. Then run `python3 scripts/zeno.py --check-plan work/<project>/plan.edl`; fix every listed problem in one edit, then check again. `[TIGHT]` and `[STYLE]` lines are advice: clear them, or keep them on purpose and say why in the segment note.
6. **Ask once.** Show the user the plan in plain words (which clip for each line, the layout, where the peaks are, every place you departed from what the script literally says, and anything you are unsure of) and ask Yes or No. Rendering takes minutes and its output goes to the user's folder, which is why you ask before it and not after.
7. **Render.** Run `python3 scripts/zeno.py --execute work/<project>/plan.edl`. Read the verify lines and `review_sheet.jpg` once. Then run **Agent 5**, the final check, as in `agent/concepts/10_five_agents.md`: zero Gemini or server calls, at most 10k tokens; script coverage (name any missing line), a tiled frame batch (layout, head alignment, faces), cut timestamps, audio. Fix and re-render at most once, and only when a check fails or something visible is wrong.
8. **Report.** Print the token table, add the row to `README.md`, and say plainly what you are unsure of (for example a voice the check doubted).

## Limits, and why
- Work only inside the Zeno folder. Finished videos go to `~/Downloads/Zeno Outputs/<video name>/`, one folder per edit.
- Zeno's code measures and renders; you do not edit Python during a job. If a tool gives a wrong result, tell the user what you saw.
- Token budget: aim for 50k, and stop and report before 70k (Agent 5 is capped at 10k and makes no API calls). Read each file once and pull extra frames only when a check fails; the budget is for editorial thinking. Cost grows with the number of tool calls (each one re-sends the whole context), so batch independent reads and commands, aim for 12 calls or fewer, and render once. Start in a fresh session with unused connectors off; never debug Zeno's Python inside an edit.
- Ask the user, rather than decide, when the footage cannot deliver what the script needs: a line no clip says, no clip with the right voice, a script cue you cannot interpret, or an effect the library does not have. Inventing a substitute hides the problem the client will see.
