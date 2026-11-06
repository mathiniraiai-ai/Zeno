# Edit rules: the Director's reference

You are the editor and `plan.edl` is yours to write. This file holds what only this project knows: the craft principles and their reasons, how to read the measurements, the emotion plan, the plan syntax, and starting values for zooms, cuts and sounds. The judgement is yours. The numbers are starting points, tuned from the owner's feedback, and where a number conflicts with what you see in the footage, the footage wins; note why in the segment.

## Craft principles
- **The script is the timeline and the contract.** Every spoken line appears once, in script order. File names and file order mean nothing. The client reviews against the script, so a missing or repeated line is the mistake they always catch.
- **Pace is tight.** Lag reads as amateur and costs retention. A conversation between two characters is back to back; a join with a quarter second of silence already feels slow.
- **Contrast makes emphasis.** A peak lands because the moments around it are calm. If every line is emphasised, none is.
- **Faces are the picture.** Keep the eyes where the eye expects them, and keep text and graphics below the face.
- **Sound serves the voice.** Effects are seasoning, always under the speech.
- **Be honest about the footage.** When it cannot deliver what the script needs (a line no clip says, no clip with the right voice), tell the user instead of covering it.

## Reading the measurements
- `line_map.md`: the whole script with its cues (scene, ✗/✓ marks, layout notes, strike-through text) and, under each line, the clip stretches that say it (`CAND`, `heard` = share of the line's words found there). It lists candidates; you choose. Script lines that are not spoken (a visual brief, character descriptions, a header note, `on screen:` text, a scene cue) have no clip and the check counts them as MISSING: record each in `decisions.txt` with its reason (`accept L2 character description, not spoken`; `accept L4 shown as banners on L6a`), and realise the useful ones another way (a scene cue becomes the transition into a split, on-screen text becomes banners). `sp=` is the speaker name exactly as the script labels it (`Lady`, `"Miss Nova"`).
- `facts.md`, per clip: every word with its start time (`1.20 word`), phrase ends (`|2.31`), pauses, `speech` spans (a voice detector that works over background noise), `voiced` spans (loudness), `emphasis` words (louder than that speaker's usual), face position (`focus`, `face_top-bottom`), and `SILENT` clips with `looks like <clip>`. Flags to trust over the word times: `NO SPEECH under '<word>'` and `TIMING WRONG` (the aligner placed the word where there is no sound). In a noisy clip `voiced` covers everything and says nothing: use `speech`.
- `clip_sheet_<n>.jpg`: three small frames of every clip. Read it once to see who is in each clip, which are silent, which are already splits or holograms, and bad frames. A silent clip named after a character belongs to them; the `looks like` hint is only a guess from colours.
- `styles/<style>/SKILL.md` and `brain_memory.json`: what the style wants. `line_map.md` shows the first rules only.

## Emotion plan (write `work/<project>/script_analysis.md` before choosing clips; 1.5 to 2.5k tokens)
One line per spoken line:

    L8 [Lady] emotion=embarrassed 2/3 | beat=mistake | zoom=none (split) | sfx=sn_wrong.wav via the red X | why: says the wrong sentence confidently
    L10 [Lady] emotion=impressed 3/3 PEAK | beat=reaction | zoom=none (split) | sfx=pop.wav on "Wow" | why: the payoff of the scene

- **emotion:** neutral, curious, joy/excited, confident, surprised/shock, tense/worried, frustrated/angry, embarrassed, sad/lonely, relieved, proud/success, with an **intensity 1 to 3**. **beat:** hook, problem, mistake, correction, reveal, tip, demo, proof, reaction, CTA.
- Read the emotion from the script (stage directions in parentheses, `[ ]` notes, punctuation, capitals, the scene's visual notes), then from the voice (`emphasis` words) and the face (clip sheet).
- Mark at most three PEAK moments in the whole video: about one per 25 to 30 seconds, so two or three in a 60 to 90 second video. Peaks get the strongest treatment (a rapid zoom and an effect); other lines get less or nothing. A line with no real emotion is `neutral`, no zoom, no effect.
- Splits, two-shots and holograms cannot zoom, so their peaks are carried by the cut and the effect. Say so in the analysis when the peaks sit there.

## Zoom: emotion decides the kind (a single static speaker only)
A rapid zoom is a snap on a word; a slow zoom is a push or a pull over the whole cut. These are a style choice added by the owner, not measured from the reference videos (which had no zoom).

| Emotion or beat | Zoom | Syntax |
|---|---|---|
| Surprise, shock, a big reveal (PEAK) | rapid in on the reaction or reveal word | `z=sudden_in:1.08@<t>`; the single biggest peak of the video may use `z=flash_zoom:1.12@<t>` (adds a brightness pulse) |
| Anger, frustration, a strong claim | rapid in, then hold | `z=sudden_in:1.08@<t>` |
| Joy, pride, success, a punchline | rapid in on the key word | `z=sudden_in:1.08@<t>` |
| Letting go after a rapid zoom | rapid out on the next cut (1.5 s or longer) | `z=sudden_out:1.08@0.10` |
| Tension, worry, suspense | slow push | `z=1->1.06` |
| Embarrassment, an awkward beat, a confession | small slow push, no sound | `z=1->1.04` |
| Sadness, loneliness, doubt | slow pull | `z=1.08->1` |
| Relief, calm, resolution | slow pull | `z=1.06->1` |
| Confidence, authority, the closing call to action | slow push | `z=1->1.05` |
| Neutral information | none; a shot over 6 s still drifts `z=1->1.03` so it is never dead | |

- A rapid zoom starts **0.05 s before the stressed word** so the snap lands on it: `@<word start - cut start - 0.05>`. Take the word from `emphasis` or from the script's key word. The snap takes about 0.18 s, then the picture holds.
- A slow zoom runs over the whole cut, so the beat needs a cut of 2 s or more. A slow push into a rapid zoom on one cut is the strongest build; keep it for a peak.
- Starting limits: at most 8 rapid zooms a minute and never two in a row; at least a quarter of the single-speaker cuts have no animated zoom (a fixed crop such as `z=1.08` counts as still) so the zooms mean something; the first cut of a hook usually opens with a rapid zoom on its first strong word; `z=1.08` fixed on the second half of a same-clip jump cut hides the jump.
- Do not zoom a split screen, a two-shot, a hologram, a pre-stitched split or a clip with deliberate camera movement such as a pan or a dolly (`z=1`); ordinary handheld shake, as in a selfie clip, is fine to zoom. The renderer holds splits still and verify fails a moving split.
- `focus=x,y` from `facts.md` keeps the eyes in place while zooming.

## Sound effects (library `assets/sfx`; `sfx:<file>@<t>/<gain dB>`, `t` relative to the cut)
One effect per beat at most, under the voice (gain -12 to -4 dB, never above 0), never on a listener-only moment. Timing uses each file's measured loudest point.

| Need | File | Place it | Gain |
|---|---|---|---|
| Light reveal, or a rapid zoom | `pop.wav` | `at` = the zoom time (loudest at 0.01 s) | -6 |
| Impact on a shock or a peak | `sn_punch_1.wav` (hit at 0.15 s) or `sn_punch_2.wav` (0.16 s) | `at` = word start - 0.15 | -4 |
| Tension building to a reveal | `riser.wav` (0.75 s, peak at its end) or `sn_riser_1.wav` (1.35 s, peak at 1.08 s) | `at` = reveal word start - 0.73 or - 1.08; only when the reveal is that far into the cut | -10 |
| Success, praise | `correct_ding.wav`, `ding.wav` (a ✓ mark already plays `correct_ding`) | on the word | -6 |
| A mistake | `sn_wrong.wav` (a ✗ mark already plays it) | end of the sentence | set by the mark |
| Transition | `whoosh.wav`, `sn_swish_1.wav`, `w42_transition.wav` | the join | -2 |
| Selection or UI feel | `click.wav` | on the word | -8 |

- The library has no laughter, gasp, heartbeat, sad sting or applause. For those moments use the footage's own sound, a 0.2 s dip into silence before the beat, or tell the user the effect is missing. New licensed files go into `assets/sfx/` and into this table.
- A transition, a mark and an emotion effect on the same join are too much: keep the strongest.
- The reference videos used no effects; the owner asked for emotion effects on purpose, so keep them sparse and let the peaks carry them.

## Cuts (times from `facts.md`, seconds in the clip)
- Start 0.06 to 0.10 s before the first kept word (at the start of the clip when the word comes earlier than that) and end 0.08 to 0.12 s after the last. A cut inside a word clips it, and the plan check blocks that. A cut that falls in a gap is moved at most 0.12 s to the quietest point at render.
- Silence across a join (end of one cut plus start of the next) of 0.20 s or less. Pauses inside one speaker's turn: cut anything over 0.30 s (split into `L5a`/`L5b`, `z=1.08` on the second half hides the jump); a breath of 0.30 s or less stays. A pause of 1.35 s or more is dead air. `--check-plan` lists the joins and pauses over these as `[TIGHT]`; Pauses of a noisy clip are in its `speech gaps` line, because the loudness method sees none there. A word flagged `NO SPEECH` is a ghost: cut across it freely and end the cut where the speech spans end.
- When a line carries a `✗` (`wrong`) or `✓` (`right`) mark, place the flashy red/green gradient (`style=flash`: `red_flash.png` / `green_flash.png`) and SFX (`sn_wrong.wav` or `correct_ding.wav`) right where the wrong or right dialogue completes, **just before the next dialogue starts**. Add `~0.70 to 1.00 s` of extra tail on that cut (`end = <last word end + 0.70..1.00 s>`) and set `mark:wrong@<last word end - cut start>/0.56@style=flash` (or `mark:right@...`) so the flash gradient and SFX complete inside that post-dialogue hold before cutting to the next speaker.
- Words repeated inside a clip (a stumble, a phrase said twice, a false start at the head): keep the last clean saying and cut in the gap before it. Repeats the script itself contains stay, as do natural reduplications (Kannada ಮತ್ತೆ ಮತ್ತೆ).
- Speech running past the script's last word is a babble to cut. A clip that opens with the word the previous cut ended on starts after that word. A note such as `(laughs mockingly)` keeps up to 1.2 s of the reaction after the last word.
- A silent beat (`(Man takes out his phone)`) uses a `SILENT` clip that shows it, chosen from the sheet, held only as long as the action needs.
- Choosing a take: the one that says the script's words completely with no stumble, then the clean clip end, then the later take. Two clips with different script lines are not takes of each other: a learner repeating the teacher's corrected sentence is a line of its own.
- Set `sp="<name>"` on every segment. The check measures the voice against it; `WRONG VOICE` means choose another take, and if no clip has the right voice, tell the user to reshoot.

## Layout
| Situation | Layout |
|---|---|
| One person talking: hook, teaching line, two people in one frame | `single` (default) |
| A clip that already is a split or a hologram (`shot_type` baked/two_shot, or the sheet) | `single`, `z=1` |
| Two characters in separate clips | `split_h` (left/right) or `split_v` (top/bottom) |
| The script names a floating window, a split or a call | follow the script |
| A language drill "How to say X?" | `chat_bar` with `chat:` |
| A remote call | `video_call` |

Writers use "vertical" and "horizontal" split loosely, so the explicit words win: `Left:`/`Right:` means `split_h`, `Top:`/`Bottom:` means `split_v`. Only when a script gives no side words, "horizontal split" means top and bottom and "vertical split" means left and right.

**Split screens with four clips (the normal case):** the left talker, the right talker, and a silent clip for each character. A character is shown talking only while they speak; the other panel shows the other character's silent clip, so a talker never appears in the listener panel. Each character keeps one panel for the whole split (`Left: A | Right: B` in the script). Give each split segment a `peer=` window: a stretch of the other character's clip in which they are not speaking. It does not have to sit at the same time as the cut; use the quiet stretches (`speech gaps` in `facts.md`). Walk forward through it turn after turn, never restarting at the same moment. When a window is shorter than the cut the renderer loops it, and a loop under a second is invisible; choose the longest quiet stretch you can, and tell the user when a loop is long. A long two-camera recording where each person is silent outside their turn can be its own listener: use the other character's quiet stretches. When a character has nothing to show, tell the user.

**Stay in the script's split for all of its dialogue**, including long teacher blocks, the payoff and the closing call to action; do not leave it for a full-frame close to get a zoom (the owner counts that as a mistake, because the client reviews against the script). Keep both characters' heads at the same height and size across every cut and every talker/listener swap: compare `face_top-bottom` of all four clips in `facts.md` before writing the split, and if a clip cannot be matched, tell the user instead of shipping a jump.

    L8 Lady.mp4 0.00-3.73 layout=split_h z=1 sp="Lady" slot=right peer="Miss Nova:Nova_silent.mp4"@4.15-7.88@left

## Transitions, marks, text
- A scene change in the script: `tr=whip_left@0.28` or `whip_right@0.28`. One or two crisp whips across clip changes in a single-speaker hook. Single into a split: `tr=warm_flash@0.35`; back out: `tr=smoothright@0.28`. No transition between turns inside a split. A whoosh goes with each whip: `sfx:whoosh.wav@0/-2`.
- `✗`/`✓` only where the script marks a line (`mark=wrong`/`mark=right` in `line_map.md`), placed right when the wrong or right sentence finishes, just before the next dialogue starts: extend the cut tail by `~0.70 to 1.00 s` after the last word, and place `mark:wrong@<last word end - cut start, relative to the cut>/0.56@pos=top-right@style=flash` (`red_flash.png` + `sn_wrong.wav`) or `mark:right@<last word end - cut start, relative to the cut>/0.56@pos=top-right@style=flash` (`green_flash.png` + `correct_ding.wav`).
- A "Stop saying" hook: `banner:"header:Stop saying"@0.3`, then one banner per phrase on its spoken word (`banner:"What do you mean?"@<word start>`). The stack is placed below the measured face automatically.
- Strike-through lines (`(X Sign and Text Strikethrough)`): `banner:"<text>"@<time>` on the spoken word. No captions, call-to-action text, B-roll or graphics that the script or the style did not ask for.

## plan.edl
`zeno.py --inspect` writes the header; you add one line per segment, in script order:

    <id> <clip file> <start>-<end> [layout=..] [z=..] [focus=x,y] [sp="Name"] [slot=..] [tr=name@sec] [peer=..] [mark:..] [banner:..] [sfx:file@sec/gain] [vol=+3dB]  # note

**A clip name that contains spaces goes in quotes**, as in `"MISS NOVA FV.mp4"`; an unquoted name makes the parser drop the whole line without a message, and the check then reports the line as unread. `id` is the script line (`L5`; `L5a`/`L5b` for parts; `L6_L7` for one cut holding two lines): the check reads the id and also compares the real words in the cut. Times inside `mark`, `banner`, `sfx` and `z=...@` are relative to the cut's start. Two lines from a real plan:

    L6a S1.mp4 0.58-9.78 z=sudden_in:1.08@0.25 focus=0.5481,0.2746 sp=Lady banner:"header:Stop saying"@0.3 banner:"What do you mean?"@1.06 banner:"I don't understand"@1.67 banner:"Repeat please"@2.89  # hook: rapid zoom on the first word, phrases on their words
    L10 Lady.mp4 4.26-8.99 layout=split_h z=1 sp=Lady slot=right peer="Miss Nova:Nova.mp4"@4.55-8.90@left sfx:pop.wav@2.13/-6  # peak: the effect carries it because a split cannot zoom

## Checks, and what to do with them
- `--check-plan` measures and does not choose. It lists plan lines it could not read first, and blocks on lines out of script order, missing or said twice, words in a cut that fit no line, cuts inside a word and a voice that does not match `sp=`. It also prints advice (`[TIGHT]` for drag, `[STYLE]` for the emotion plan) and never blocks on that. If something is deliberate, record it in `work/<project>/decisions.txt` as `accept L2 <reason>`.
- After rendering, `verify` checks order, loudness, the cuts, the picture against the plan, black frames, banners on faces and faces in every panel (`[FRAMES]` with the local face detector, no model tokens), and builds `review_sheet.jpg` (three small frames per cut). Read the sheet once; look at more frames only for a failure or a visual problem the user reports, and then only those moments.
- Then show the user the plan and ask the one Yes/No. `--execute` runs the same plan check again before it renders.
