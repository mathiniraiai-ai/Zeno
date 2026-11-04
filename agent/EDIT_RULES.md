# Edit rules: how the Director makes every decision

Zeno transcribes, measures and renders. **You, the Director, make every editing decision and write `plan.edl`.** Nothing is drafted for you. Budget: **50k to 70k tokens per video**. Spend them on looking properly, not on shortcuts.

## Read these, in this order
1. `work/<project>/line_map.md`: the full script with its cues; under each line, every clip stretch that says it (`CAND`, `heard`).
2. `work/<project>/facts.md`: every word with its start time, phrase ends (`|2.31`), pauses, faces, `SILENT` clips.
3. `work/<project>/clip_sheet_<n>.jpg`: pictures of every clip. Look at them: who is in each clip, silent clips, pre-stitched splits, bad frames.
4. `styles/<style>/SKILL.md` and `styles/<style>/brain_memory.json` (the full learned rules; `line_map.md` only shows their start).

## Decide in this order
1. **Timeline.** The script is the timeline. Go line by line, top to bottom. File names and file order mean nothing.
2. **Take.** For each line compare every `CAND` in `facts.md`. Prefer the take that says the script's words completely, with no stumble or repeat, then the clean clip end, then the later take. Never assume first or last is right. Two clips with different script lines are not takes of each other: a learner repeating the teacher's corrected sentence is a line of its own.
3. **Speaker.** Use the clip of the character the script names. A clip's file name or the sheet tells you who it is. Set `sp="<name>"` on every segment: the check measures the voice against it. `WRONG VOICE` means pick another take; if no clip has the right voice, tell the user to reshoot.
4. **Cuts.** See below.
5. **Layout.** See below.
6. **Motion, transitions, marks, SFX.** See below.
7. **Self-review.** Read your plan against the script once as an editor would: does each cut land, does anyone speak twice, does the eye have somewhere to go? Then run `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes.

## Cuts (times from `facts.md`, seconds in the clip)
- Start 0.10 to 0.18 s before the first kept word; end 0.12 to 0.18 s after the last. Stay inside the clip and never cut inside a word: the check blocks it. A cut in a gap is moved to the gap's quietest 10 ms at render, so no click.
- Trim dead air and any "actor getting ready" stare before the first word or after the last.
- **Retakes inside a clip:** when words repeat (`Almost, almost`, a phrase said twice, a false start at the head) keep the last clean saying. Cut in the gap before it. A repeat the script itself contains stays, and so do natural reduplications (Kannada ಮತ್ತೆ ಮತ್ತೆ, ಪದೇ ಪದೇ).
- **Babble after the line:** if speech continues past the script's last word, end before it.
- **Overlap between clips:** if a clip opens with the word the previous cut ended on, start after that word.
- **Internal pauses:** cut pauses over about 0.85 s inside one speaker's turn: split into `L5a` and `L5b`, and put `z=1.08` on the second part to hide the jump. Pauses of 1.35 s or more are always dead air. Natural turns between two characters can stay up to about 1.3 s.
- A note like `(laughs mockingly)` on a line: keep up to 1.2 s of the reaction after the last word.
- **Silent beat** (`(Man takes out his phone)`): use a `SILENT` clip that shows it, chosen by looking at the sheet; hold it for what the action needs.

## Layout
| Situation | Layout |
|---|---|
| One person talking, hook, teaching line, two people in one frame | `single` (default) |
| A clip that is already a split or a hologram (`shot_type` shows baked/two_shot, or the sheet) | `single`, `z=1` |
| Two characters in separate clips | `split_v` (top/bottom) or `split_h` (left/right) |
| The script says floating window, split, call | follow the script |
| Language drill "How to say X?" | `chat_bar` with `chat:` |
| Remote call | `video_call` |

"Horizontal split" means top and bottom. "Vertical split" means left and right.

**Split screens and the listener rule.** While one character speaks, the other panel shows the other character, silent and listening. A character keeps one panel for the whole split: read `Top: A | Bottom: B`, or `Woman - Right screen`. Give every split segment `slot=` for the speaker and a `peer=` for the listener:

    L8 S07_Lady1.mp4 0.00-3.73 layout=split_v z=1 sp="Lady 1" slot=bottom peer="Robot:S18_Robot.mp4"@4.15-4.95@top

`peer="<character>:<clip>"@<from>-<to>@<panel>`. Use that character's `SILENT` clip. Walk through it turn after turn: never restart at the same moment. With no silent clip use a pause from a stretch of their own clip where they are not speaking, and report it to the user. Never show speech in the listener panel. If a character has nothing to show, tell the user.

## Zoom
- **Never** zoom split screens, two-shots, holograms, pre-stitched splits or clips with camera movement: `z=1`.
- Single static speaker only:
  - First single segment: `z=sudden_in:1.08@<word time>`, on the first strong word, about 0.25 s in.
  - Sudden zoom-in on a Supernova mention, a key teaching point or the punch word; not twice in a row.
  - After a sudden-in, `sudden_out` or a slow push on the next shot of 1.5 s or more.
  - Any single shot over 6 s moves: `z=1->1.06`, or `z=1.08->1` as a pull-out. A closing call to action: `z=1->1.05`.
  - `z=1.08` on the second half of a same-clip jump cut.
- Focus: `focus=x,y` from `facts.md`, to keep the eyes where they are.

## Transitions
- Scene change in the script: `tr=whip_left@0.28` or `whip_right@0.28`.
- Single to split: `tr=warm_flash@0.35`. Split back to single: `tr=smoothright@0.28`.
- One or two crisp whips across clip changes in a single-speaker hook.
- **Never** a transition between turns inside a split.
- Add `sfx:whoosh.wav@0.0` on whips.

## Marks and text
- `✗`/`✓` only where the script marks a line (`mark=wrong` / `mark=right` in `line_map.md`). Place at the **end of the sentence**: `mark:wrong@<last word end, relative to the cut>/0.56@pos=top-right@style=flash`, and keep at least 0.6 s of cut after that moment.
- Strike-through lines: `banner:"<text>"@<time>`, on the spoken word. "Stop saying" hook: stack one banner per phrase, each on its word.
- No captions, CTAs, B-roll or text the script or the style does not ask for.

## plan.edl
`zeno.py --inspect` writes the header. Add one line per segment, in script order:

    <id> <clip file> <start>-<end> [layout=..] [z=..] [focus=x,y] [sp="Name"] [slot=..] [tr=name@sec] [peer=..] [mark:..] [banner:..] [sfx:file@sec] [vol=+3dB]  # note

- `id` is the script line: `L5`, `L5a`/`L5b` for parts, `L6_L7` for one cut holding two lines. Keep these ids: the check reads them and also compares the real words in the cut.
- Times inside `mark`, `banner`, `sfx` and `z=sudden_in:..@` are relative to the cut's start.

## The check
`--check-plan` measures, it does not choose. It blocks on: lines out of script order, missing or said twice, words in a cut that fit no line, cuts inside a word, and a voice that does not match `sp=`. Fix the plan. If something is deliberate, write it in `work/<project>/decisions.txt`:

    accept L2 no clip for this beat; user agreed

Then show the user the plan and ask the one Yes/No. Render with `--execute`, which runs the same check again.
