# Edit rules: how the Director makes every decision

Zeno transcribes, measures and renders. **You, the Director, make every editing decision and write `plan.edl`.** Nothing is drafted for you. Budget: aim for **50k tokens per video**; **70k is the hard border**. Spend them on looking properly, not on shortcuts.

## Read these, in this order
1. `work/<project>/line_map.md`: the full script with its cues; under each line, every clip stretch that says it (`CAND`, `heard`).
2. `work/<project>/facts.md`: every word with its start time, phrase ends (`|2.31`), pauses, faces, `SILENT` clips.
3. `work/<project>/clip_sheet_<n>.jpg`: pictures of every clip. Look at them: who is in each clip, silent clips, pre-stitched splits, bad frames.
4. `styles/<style>/SKILL.md` and `styles/<style>/brain_memory.json` (the full learned rules; `line_map.md` only shows their start).

## Decide in this order
0. **Analyse the script (emotion plan). Do this before choosing a single clip.** See "Emotion plan" below. It decides where the video speeds up, where it breathes, which zooms are rapid or slow, and where an effect sound goes.
1. **Timeline.** The script is the timeline. Go line by line, top to bottom. File names and file order mean nothing.
2. **Take.** For each line compare every `CAND` in `facts.md`. Prefer the take that says the script's words completely, with no stumble or repeat, then the clean clip end, then the later take. Never assume first or last is right. Two clips with different script lines are not takes of each other: a learner repeating the teacher's corrected sentence is a line of its own.
3. **Speaker.** Use the clip of the character the script names. A clip's file name or the sheet tells you who it is. Set `sp="<name>"` on every segment: the check measures the voice against it. `WRONG VOICE` means pick another take; if no clip has the right voice, tell the user to reshoot.
4. **Cuts.** See below.
5. **Layout.** See below.
6. **Apply the emotion plan:** zooms, effect sounds, transitions, marks. Take the exact word times from `facts.md` (`emphasis` words are where the speaker leaned in).
7. **Self-review.** Read your plan against the script once as an editor would: does each cut land, does anyone speak twice, does the eye have somewhere to go, do the biggest moments get the biggest treatment? Then run `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes (`[STYLE]` and `[TIGHT]` lines are advice: clear them or keep them on purpose).

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

## Emotion plan (write `work/<project>/script_analysis.md` first; about 1.5 to 2.5k tokens)
Read the whole script once (it is in `line_map.md`) and write one line per spoken line:

    L8 [Lady] emotion=embarrassed 2/3 | beat=mistake | zoom=slow push 1->1.05 | sfx=none | why: says the wrong sentence confidently, then the red X
    L9 [Miss Nova] emotion=warm 1/3 | beat=correction | zoom=none | sfx=none | why: calm teacher, let the face breathe
    L10 [Lady] emotion=impressed 3/3 PEAK | beat=reaction | zoom=rapid in 1.08 on "Wow" | sfx=pop at the zoom | why: the payoff of the scene

- **emotion** (pick one) and **intensity 1 to 3:** neutral, curious, joy/excited, confident, surprised/shock, tense/worried, frustrated/angry, embarrassed, sad/lonely, relieved, proud/success.
- **beat:** hook, problem, mistake, correction, reveal, tip, demo, proof, reaction, CTA.
- Read emotion from the script itself: stage directions in (parentheses) and `[ ]` notes, punctuation (`!`, `?`, `...`), capitals, quoted words, the scene's visual notes; then from the speaker's voice (`emphasis` words in `facts.md`) and the face (clip sheet).
- **Mark at most 3 PEAK moments in the whole video** (about 1 per 30 s). The peaks get the strongest treatment, a rapid zoom plus an effect sound. Everything else gets less, or nothing. Contrast between calm and peak is what makes a peak land; a video where everything is emphasised has no emphasis.
- Keep it honest: a line with no real emotion is `neutral`, zoom `none`, sfx `none`. Do not decorate it.

## Zoom: emotion decides the kind (single static speaker only)
**Two kinds.** A *rapid* zoom is a snap on a word. A *slow* zoom is a push or a pull over the whole cut. Start from these values and tune them from the owner's feedback (they are a style choice, not measured from the reference videos):

| Emotion or beat | Zoom | Plan syntax | Why |
|---|---|---|---|
| Surprise, shock, a big reveal (PEAK) | **Rapid in** on the reaction or reveal word | `z=sudden_in:1.08@<t>`; the one biggest PEAK of the video may use `z=flash_zoom:1.12@<t>` (adds a brightness pulse) | the snap is the jolt |
| Anger, frustration, a strong claim | **Rapid in**, then hold | `z=sudden_in:1.08@<t>` | pressure |
| Joy, pride, success, a punchline | **Rapid in** on the key word | `z=sudden_in:1.08@<t>` | the beat lands |
| Release right after a rapid zoom | **Rapid out** | `z=sudden_out:1.08@0.10` on the next cut (1.5 s or longer) | lets go of the tension |
| Tension, worry, fear, suspense | **Slow push** | `z=1->1.06` | the room closes in |
| Embarrassment, an awkward beat, confession | **Slow push**, small | `z=1->1.04` | uncomfortable closeness; no sound |
| Sadness, loneliness, doubt | **Slow pull** | `z=1.08->1` | distance |
| Relief, calm, resolution | **Slow pull** | `z=1.06->1` | the breath out |
| Confidence, authority, the closing call to action | **Slow push** | `z=1->1.05` | steady and warm |
| Neutral information | none; a shot over 6 s still drifts `z=1->1.03` | | never leave a long shot dead |

**Where exactly**
- A rapid zoom starts **0.05 s before the stressed word** so the snap lands on it: `@<word start - cut start - 0.05>`. Take the word from `emphasis` in `facts.md`, or from the script's key word. It takes about 0.18 s, then the picture holds.
- A slow zoom runs over the whole cut, so give the beat a cut of **2 s or more**. A slow push into a rapid zoom on the same cut is the strongest build: use it only for a PEAK.
- **Limits (starting values):** no more than 8 rapid zooms a minute and never two in a row; at least a quarter of the single-speaker cuts stand still, so the zooms mean something; one `flash_zoom` in the whole video at most; the first cut of the hook usually opens with a rapid zoom on its first strong word.
- **Never** zoom a split screen, a two-shot, a hologram, a pre-stitched split or a clip with camera movement: `z=1`. (They keep their own energy from the cut and the effect.)
- `z=1.08` (a fixed punch-in) on the second half of a same-clip jump cut hides the jump.
- Keep the eyes in place: `focus=x,y` from `facts.md`.

## Sound effects for emotion (library: `assets/sfx`, `sfx:<file>@<t>/<gain dB>`, `t` relative to the cut)
Effects are seasoning. One per beat at most, always under the voice (gain -12 to -4 dB, never above 0), never on a listener-only moment.

| Need | File | Where | Gain |
|---|---|---|---|
| Light reveal or a rapid zoom | `pop.wav` | its loudest point is at 0.01 s: `at` = the zoom time | -6 |
| Impact on a shock or a PEAK | `sn_punch_1.wav` (hit 0.15 s in); `sn_punch_2.wav` (0.16 s) | `at` = word start **- 0.15**, so the hit lands on the word | -4 |
| Tension building to a reveal | `riser.wav` (0.75 s, peak at its end) or `sn_riser_1.wav` (1.35 s, peak at 1.08 s) | `at` = reveal word start **- 0.73** (riser) or **- 1.08** (sn_riser); only when the reveal is at least that far into the cut | -10 |
| Success, praise, "right" | `correct_ding.wav` (also what a ✓ mark plays), `ding.wav` | on the word | -6 |
| Wrong, a mistake | `buzzer.wav` (also what a ✗ mark plays) | at the end of the sentence | marks set it |
| Transition | `whoosh.wav`, `sn_swish_1.wav`, `w42_transition.wav` | the join | -2 |
| UI or selection feel | `click.wav` | on the word | -8 |

- **Not in the library:** laughter, a gasp, a heartbeat, a sad sting, applause. For those moments use the footage's own sound, a 0.2 s dip in silence before the beat, or tell the user the effect is missing. Do not pretend another file is one. New files (licensed, or the owner's own) go in `assets/sfx/` and into this table.
- A transition, a mark and an emotion effect on the same join are too much: choose the strongest.
- The reference videos used no effects at all. The user asked for emotion effects on purpose: keep them sparse and let the peaks carry them.

## Transitions
- Scene change in the script: `tr=whip_left@0.28` or `whip_right@0.28`.
- Single to split: `tr=warm_flash@0.35`. Split back to single: `tr=smoothright@0.28`.
- One or two crisp whips across clip changes in a single-speaker hook.
- **Never** a transition between turns inside a split.
- Add `sfx:whoosh.wav@0.0/-2` on whips.

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

## The frame-by-frame check (after rendering)
`verify` looks at the finished video every quarter second with the local face detector (no model tokens): `[FRAMES]` fails on black or flat frames and on a banner covering a face, and warns when a panel has no face for more than a third of its frames. Fix a failure in `plan.edl` (move the banner's cut, change the zoom) and render again; treat a warning as a prompt to look at that moment on `review_sheet.jpg`.

## The check
`--check-plan` measures, it does not choose. It prints `[TIGHT]` advice (never blocks) and blocks on: lines out of script order, missing or said twice, words in a cut that fit no line, cuts inside a word, and a voice that does not match `sp=`. Fix the plan. If something is deliberate, write it in `work/<project>/decisions.txt`:

    accept L2 no clip for this beat; user agreed

Then show the user the plan and ask the one Yes/No. Render with `--execute`, which runs the same check again.
