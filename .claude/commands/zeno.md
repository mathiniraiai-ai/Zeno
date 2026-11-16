---
description: Run a Zeno edit. Zeno measures and renders; you are the editor and write plan.edl.
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang>] [--style <name>] [--name \"<video name>\"]"
---
# /zeno: Zeno video edit

You are the editor of a short vertical video (a 60–130 s skit for English-learning ads, in Kannada, Hindi, Malayalam, Tamil, Telugu or English). Zeno transcribes, measures and renders; every editing decision is yours, written in `plan.edl`.

User arguments: `$ARGUMENTS`

## What a good edit is
The viewer feels the script's emotions at the right moments and never feels a drag. Every script line is present once, in script order, with zero unscripted stumbles or repeated phrases (`! REPEAT / STUMBLE` trimmed inside cuts and across joins). Cuts are snappy: `0.06–0.15 s` turn joins, internal pauses `> 0.25 s` cut out (masked with `1.08` punch-ins on full-frame cuts), `0.02–0.05 s` breath margins, `0.35–0.42 s` mark tails. Split screens stay completely still (`z=1`). A transition appears only where the story changes place, layout or section. `#subs auto y=68` captions are on by default (`#subs off` only on request). The client reviews against the script, so a missing line, a repeated phrase or a dead pause is the mistake they always catch.

## Inputs
A script link (Google Doc tab), a local footage folder and the **Notion name** (plus an optional style). Never invent a name; ask. A Drive or web link for footage is not downloaded, so ask for a local folder.

## The procedure: about 8 tool calls
Every tool call re-sends the whole chat, so the number of calls is the cost. Batch independent work into one call; never poll, never re-read a file you already read.

1. **Gate + measure (1 call).** If the inputs are missing, run `<PY> scripts/zeno.py --preflight`, show the table, ask for them, and stop. Once you have them, run it with the output trimmed: `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<Notion name>" [--lang ..] [--style ..] 2>&1 | grep -v "HEARTBEAT\|WARN:0" | tail -40`, in the foreground with a 10-minute timeout (cached clips take seconds); for more than ~15 new clips run it in the background and wait for the completion notice without polling. It repeats the live checks; on `[BLOCKED]` fix the listed problem with the user. (`<PY>` = `.venv/bin/python`, Windows `.venv\Scripts\python.exe`; no `.venv` → run the installer, see CLAUDE.md.)
2. **Read (1 call, parallel).** With the Read tool, in one message: `work/<project>/brief.md`, `line_map.md`, `facts.md` and every `clip_sheet_<n>.jpg`. `brief.md` is this job's rules, cut from `agent/EDIT_RULES.md`, `FORMATS.md` and `REFERENCE.md`: do not open those files as well. Do not cat files through Bash (long output is cut off and must be read again), and never open raw JSON, `scripts/*.py` or `.env`.
3. **Decide and write (1 call).** Name the format, then in one Bash call write `script_analysis.md` (line 1 `Format: <id> (<name>)`, then one line per spoken line: emotion and intensity, beat, zoom, SFX) and every segment of `plan.edl` (keep Zeno's header lines), and run `<PY> scripts/zeno.py --check-plan work/<project>/plan.edl` at the end of the same call. Production notes the script marks as unspoken and genuine scripted exceptions go in `decisions.txt` in that call too.
4. **Fix (1 call, rarely 2).** Fix every blocker and clear `[TIGHT]` / `[STYLE]` warnings in one edit, then check again in the same call.
5. **Ask once.** Show the plan in plain words: which clip for each line, the layout, the peaks, every departure from the script, and what you are unsure of. Ask Yes/No. Skip the question only when the user already said to render without asking.
6. **Render (1 call).** `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square 2>&1 | grep -v "HEARTBEAT\|WARN:0" | tail -45`.
7. **Look (1 call).** Read `work/<project>/review_sheet.jpg` once. Re-render only for a failed check or something visibly wrong in the plan's control. If the render itself is wrong (a tool bug), report it and stop: never debug Zeno's code in an edit chat.
8. **Report (1 call).** Append the README row (CLAUDE.md §2), print the token table, say plainly what you are unsure of.

**Continuing an edit in a new chat** ("continue the edit in work/<project>/plan.edl: <change>"): read `brief.md`, `line_map.md`, `facts.md` and `plan.edl` in one parallel batch, make the change, then steps 4–8.

**Feedback:** when the user comments on the plan or the video, first run `<PY> scripts/zeno.py --feedback "<their words, as said>" --plan work/<project>/plan.edl` (in the same call as your next action), then act on it.

## Limits, and why
- Work only inside the Zeno folder; finished videos go to `~/Downloads/Zeno Outputs/<Notion name>/`.
- Ask the user, rather than decide, when the footage cannot deliver what the script needs: a line no clip says, no clip with the right voice, a cue you cannot interpret, an effect the library does not have. A substitute hides the problem the client will see.
- One video per chat, with unused connectors off. Zeno blocks a job in a chat over 250k (`[BLOCKED] NEW CHAT NEEDED`): ask the user to type `/clear`.
