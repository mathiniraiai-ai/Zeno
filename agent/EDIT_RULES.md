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

## Cuts: tight, never laggy (times from `facts.md`, seconds in the clip)
- **Use the real sound, not only the word times.** `facts.md` lists `speech` spans (a voice detector that works over background noise) and `voiced` spans (loudness). It flags `NO SPEECH under '<word>'` and `TIMING WRONG` words: believe the spans over a flagged word and never build a cut on it. In a noisy clip (selfie on a road) `voiced` covers the whole clip and is useless: use `speech`.
- Start **0.06 to 0.10 s** before the first kept word and end **0.08 to 0.12 s** after the last. Never inside a word (the check blocks it). A cut in a gap is nudged by at most 0.12 s to its quietest point.
- **Silence across a join** (end of one cut + start of the next) must be **0.20 s or less**. A split-screen conversation is back to back: hand over on the breath, not after a gap.
- **Pauses inside a cut:** cut any pause over **0.30 s** inside one speaker's turn (split into `L5a`/`L5b`, `z=1.08` on the second half to hide the jump). Keep a breath of 0.30 s or less. Pauses of 1.35 s or more are always dead air.
- `--check-plan` prints `[TIGHT]` lines for every join and pause over these limits. Clear each one by moving a time, or leave it only when it is a deliberate beat (and say why in the note).
- **Marks must not make a gap.** A ✗/✓ flash needs 0.56 s, but start it **0.15 s before the sentence ends** (`at = last word end - 0.15`) so the cut can end about 0.4 s after the last word, not 0.6 s.
- **Retakes inside a clip:** when words repeat (`Almost, almost`, a phrase said twice, a false start at the head) keep the last clean saying. Cut in the gap before it. A repeat the script itself contains stays, and so do natural reduplications (Kannada ಮತ್ತೆ ಮತ್ತೆ, ಪದೇ ಪದೇ).
- **Babble after the line:** if speech continues past the script's last word, end before it.
- **Overlap between clips:** if a clip opens with the word the previous cut ended on, start after that word.
- A note like `(laughs mockingly)` on a line: keep up to 1.2 s of the reaction after the last word.
- **Silent beat** (`(Man takes out his phone)`): use a `SILENT` clip that shows it, chosen by looking at the sheet; hold it only as long as the action needs.

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

**Split screens: four clips (the normal case).** The user gives four clips: the left talker, the right talker, and one silent clip for each of them (the character listening). Rules:
- Each talker is shown **only while they speak**. When the left character speaks, the right panel shows the **right character's silent clip**, and the other way round. A character keeps one panel for the whole split (`Left: A | Right: B` in the script, or `Top`/`Bottom`).
- `facts.md` marks silent clips `SILENT` and says `looks like <clip>` (the speaking clip with the same person and place). Check it on the clip sheet before you trust it. A silent clip named after the character wins over the guess.
- **Stitch the silent clips in the gaps.** Give each split segment a `peer=` window of the other character's silent clip, and **walk forward through it** turn after turn (the second turn starts where the first ended). Never restart it at the same moment. If a silent clip is shorter than all the turns, loop it only after it has played once, and tell the user.
- A talking clip that is itself a long recording of the whole conversation (this character is silent outside their turns, as in a real two-camera shoot) can serve as its own listener: use the other character's quiet stretches. Prefer the dedicated silent clip when there is one.
- Never show speech in the listener panel. If a character has nothing to show, tell the user.

    L8 S07_Lady.mp4 0.00-3.73 layout=split_h z=1 sp="Lady" slot=right peer="Miss Nova:Nova_silent.mp4"@4.15-7.88@left

`peer="<character>:<silent clip>"@<from>-<to>@<panel>`.

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
- `✗`/`✓` only where the script marks a line (`mark=wrong` / `mark=right` in `line_map.md`). Flash at the end of the sentence, started 0.15 s before the last word ends: `mark:wrong@<last word end - 0.15, relative to the cut>/0.56@pos=top-right@style=flash`.
- **"Stop saying" hook:** a `banner:"header:Stop saying"@0.3` title, then one banner per phrase, each on its spoken word (`banner:"What do you mean?"@<word start>`). The stack is placed **below the face** automatically, from the measured face box (`face_top-bottom` in `facts.md`, adjusted for the zoom). Never on the face. Look at the frame to confirm.
- Strike-through lines (`(X Sign and Text Strikethrough)`): `banner:"<text>"@<time>`, on the spoken word.
- No captions, CTAs, B-roll or text the script or the style does not ask for.

## Looking at pictures (cheap, and only when it adds something)
- Read the clip sheets **once**, before editing (`clip_sheet_<n>.jpg`: 3 frames per clip, about 0.5 to 2k tokens a sheet). Use them to see who is in a clip, silent clips, baked splits, bad frames.
- After rendering, read `review_sheet.jpg` once (3 small frames per cut, labelled with the cut id and time, about 1k tokens): check banners are off the face, the split panels show the right character, the listener is not speaking, and marks and transitions look right. Pull more frames only when a check fails or the user reports a visual problem, and then only the few moments in question.

## plan.edl
`zeno.py --inspect` writes the header. Add one line per segment, in script order:

    <id> <clip file> <start>-<end> [layout=..] [z=..] [focus=x,y] [sp="Name"] [slot=..] [tr=name@sec] [peer=..] [mark:..] [banner:..] [sfx:file@sec] [vol=+3dB]  # note

- `id` is the script line: `L5`, `L5a`/`L5b` for parts, `L6_L7` for one cut holding two lines. Keep these ids: the check reads them and also compares the real words in the cut.
- Times inside `mark`, `banner`, `sfx` and `z=sudden_in:..@` are relative to the cut's start.

## The check
`--check-plan` measures, it does not choose. It prints `[TIGHT]` advice (never blocks) and blocks on: lines out of script order, missing or said twice, words in a cut that fit no line, cuts inside a word, and a voice that does not match `sp=`. Fix the plan. If something is deliberate, write it in `work/<project>/decisions.txt`:

    accept L2 no clip for this beat; user agreed

Then show the user the plan and ask the one Yes/No. Render with `--execute`, which runs the same check again.
