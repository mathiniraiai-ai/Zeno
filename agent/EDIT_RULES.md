# Edit rules: the Director's reference

You are the editor and `plan.edl` is yours to write. This file holds what only this project knows: the craft principles and their reasons, how to read the measurements, the emotion plan, the plan syntax, and starting values for zooms, cuts and sounds. The judgement is yours. The numbers are starting points, tuned from the owner's feedback, and where a number conflicts with what you see in the footage, the footage wins; note why in the segment.

## Craft principles
- **The script is the timeline and the contract.** Every spoken line appears once, in script order, with zero unscripted stumbles or repeated words. File names and file order mean nothing. The client reviews against the script, so a missing line, repeated phrase, or untrimmed false start is the mistake they always catch.
- **Pace is snappy.** Lag reads as amateur and costs retention. Dialogue turns join tightly (`0.06 to 0.15 s`, never over `0.20 s` dead air unless a dramatic script beat asks for it), and internal pauses over `0.25 s` inside monologues or long split turns (`> 5 s`) are cut out and masked with an instant `1.08` punch-in.
- **Contrast makes emphasis.** Peaks and emotional turns land because calm setup moments stay composed (`1.0`); split screens are always still.
- **Faces are the picture.** Keep the eyes where the eye expects them, keep banners and `#subs auto y=68` captions clear of faces (`y=50` automatically on the `split_v` seam), and keep both split-screen panels steady at `1.0` in one fixed frame.
- **Sound punctuates the beats.** SFX are peak-normalized to `-1 dBFS` and mixed post-`loudnorm`, so `-4 to 0 dB` cuts cleanly through phone speakers without drowning speech.
- **Be honest about the footage.** When it cannot deliver what the script needs (a line no clip says, no clip with the right voice), tell the user instead of covering it.

## Reading the measurements
- `line_map.md`: the whole script with its cues (scene, ✗/✓ marks, layout notes, strike-through text) and, under each line, the clip stretches that say it (`CAND`, `heard` = share of the line's words found there, plus any `[REPEAT]` warning). It lists candidates; you choose. Script lines that are not spoken (a visual brief, character descriptions, a header note, `on screen:` text, a scene cue) have no clip and the check counts them as MISSING: record each in `decisions.txt` with its reason (`accept L2 character description, not spoken`; `accept L4 shown as banners on L6a`), and realise the useful ones another way (a scene cue becomes the transition into a split, on-screen text becomes banners). `sp=` is the speaker name exactly as the script labels it (`Lady`, `"Miss Nova"`).
- `facts.md`, per clip: every word with its start time (`1.20 word`), phrase ends (`|2.31`), pauses, `speech` spans (a voice detector that works over background noise), `voiced` spans (loudness), `emphasis` words (louder than that speaker's usual), face position (`focus`, `face_top-bottom`), and `SILENT` clips with `looks like <clip>`. Flags to trust and act on immediately: `! REPEAT / STUMBLE` (an unscripted false start, stutter, or repeated phrase — trim it out both inside cuts and across adjacent cut joins), `NO SPEECH under '<word>'` and `TIMING WRONG` (the aligner placed the word where there is no sound). In a noisy clip `voiced` covers everything and says nothing: use `speech`.
- `clip_sheet_<n>.jpg`: three small frames of every clip. Read it once to see who is in each clip, which are silent, which are already splits or holograms, and bad frames. A silent clip named after a character belongs to them; the `looks like` hint is only a guess from colours.
- `styles/<style>/SKILL.md` and `brain_memory.json`: what the style wants. `line_map.md` shows the first rules only.

## Emotion plan (write `work/<project>/script_analysis.md` before choosing clips; 1.5 to 2.5k tokens)
One line per spoken line:

    L8 [Lady] emotion=embarrassed 2/3 | beat=mistake | zoom=none (split) | sfx=sn_wrong.wav via the red X | why: says the wrong sentence confidently
    L10 [Lady] emotion=impressed 3/3 PEAK | beat=reaction | zoom=none (split) | sfx=pop.wav/-3 on "Wow" | why: the payoff of the scene

- **emotion:** neutral, curious, joy/excited, confident, surprised/shock, tense/worried, frustrated/angry, embarrassed, sad/lonely, relieved, proud/success, with an **intensity 1 to 3**. **beat:** hook, problem, mistake, correction, reveal, tip, demo, proof, reaction, CTA.
- Read the emotion from the script (stage directions in parentheses, `[ ]` notes, punctuation, capitals, the scene's visual notes), then from the voice (`emphasis` words) and the face (clip sheet).
- Scale PEAK moments (`3/3 PEAK`) with video length — roughly **one PEAK every `12 to 18 s`** (`3 to 6` peaks in a `60 to 90 s` reel). Peaks get the strongest treatment (`sudden_in:1.08` or one `flash_zoom:1.12` + punch/pop/riser SFX at `-3 to 0 dB`); `2/3` emotional/teaching turns get smooth push-ins (`1.0->1.06` / `1.0->1.07`) or crisp callout snaps; calm `1/3` `neutral` setup lines stay `z=1.0` so the moves land with contrast.
- **Split screens never zoom.** On `split_h`, `split_v` and pre-stitched/baked splits (`slot=`/`active=`), write `z=1` on every segment: no push-in, no `sudden_in`, no `1.08` step, no pull-out. Each character has a speaking clip and a silent clip made from the same picture, and the panel switches between them on every turn; any zoom makes the same person jump in size at the switch. The renderer ignores a zoom written on a split and holds each panel in one fixed frame (14% headroom, background lined up between the speaking and the silent clip). Emphasis in a split comes from the cut, the words, the marks and the sound, not from motion.
- **Captions (owner's rule, overrides any older wording in this file).** `#subs auto y=68` is the default. One style is used for the whole video, at one fixed size (54 px on a 1080-wide picture; `size=` changes it), always on a single row (never two rows), with no per-caption colour changes unless the plan says `adapt=on`. On full-frame cuts captions sit at 65 to 70% of the height (`y=68`). In a top/bottom split they always sit on the divider line in the middle (`y=50`), whatever `y=` says, so neither face is covered.

## Zoom: emotion decides the kind (full-frame cuts only; splits never zoom)
A rapid zoom is a snap on a word; a slow zoom is a push or a pull over the whole cut. Zooms belong to `single` (full-frame) cuts only. A split screen stays completely still (`z=1`).

| Emotion or beat | Zoom | Syntax |
|---|---|---|
| Surprise, shock, a big reveal (PEAK) | rapid in on the reaction or reveal word | `z=sudden_in:1.08@<t>`; the single biggest full-frame peak may use `z=flash_zoom:1.12@<t>` (never in a split) |
| Anger, frustration, a strong claim | rapid in, then hold | `z=sudden_in:1.08@<t>` |
| Joy, pride, success, a punchline | rapid in on the key word | `z=sudden_in:1.08@<t>` |
| Letting go after a rapid zoom | rapid out on the next cut (1.5 s or longer) | `z=sudden_out:1.08@0.10` |
| Tension, worry, suspense (`>= 2.5 s`) | slow push | `z=1->1.06` (or `z=1->1.07`) |
| Embarrassment, an awkward beat, a confession | small slow push, no sound | `z=1->1.04` |
| Sadness, loneliness, doubt | slow pull | `z=1.08->1` |
| Relief, calm, resolution | slow pull | `z=1.06->1` |
| Confidence, authority, the closing call to action | slow push | `z=1->1.06` |
| Neutral information | `1.0` on short `< 2.5 s` beats; turns `>= 4 s` drift `z=1->1.05` so no shot feels dead | |

- A rapid zoom starts **0.05 s before the stressed word** so the snap lands on it: `@<word start - cut start - 0.05>`. Take the word from `emphasis` or from the script's key word. The snap takes about 0.18 s, then the picture holds.
- A slow zoom runs over the whole cut (`>= 2.5 s`). A slow push into a rapid zoom on one cut is a strong build for a peak.
- Starting limits: up to 16 rapid zooms a minute and avoid two rapid `sudden_in` snaps back-to-back without releasing (`sudden_out:1.08@0.10`, `1.06->1`, or a calm `1.0` cut); keep roughly a quarter of short setup cuts at `1.0` for contrast; open hooks with `sudden_in:1.08` on the first strong word; use `z=1.08` step zoom on the second half (`L4b`) of any pause-trim jump cut (`L4a` -> `L4b`) in `single` only. In a split, do not cut a pause shorter than `0.5 s` (a cut in a still panel shows as a jump); keep it as a natural beat.
- **No zoom in splits, and other locks:** `split_h`, `split_v` and baked-split segments are always `z=1`. In `single`, lock `z=1.0` on 3D holograms, clips with deliberate camera pans/dollies (handheld selfie shake is fine to zoom), and shared single-camera two-shots where `facts.md` reports `none` for safe face-keeping zoom (otherwise safe live two-shots may zoom up to `1.10` using `focus=x,y` from `facts.md`).
- `focus=x,y` from `facts.md` keeps the eyes in place while zooming.

## Ad tempo for Meta ads (motion in full-frame, stillness in splits)
Every edit is built for high-retention Reels / Meta ads, so the opening live scenes move, and the split-screen lesson holds attention through tight turns, marks and sound while the picture stays still:
- **Active motion in full-frame only:** at least 60% of live `single` cuts carry an active zoom (`1.0->1.06` / `1.0->1.07` push-in, `sudden_in:1.08@<beat>` punch-in, `sudden_out`, or `1.08` pause-cut step) or open with a transition, while short reaction beats stay at `1.0` for contrast. Split-screen turns are always `z=1`.
- **Breathe in and out:** punch in on emphasis words or punchlines (`z=sudden_in:1.08@<t>`), push in smoothly (`1.0->1.06` / `1.0->1.07`) across teaching explanations `>= 2.5 s`, and release (`z=sudden_out:1.08@0.10` or `z=1.06->1`) on follow-up reactions.
- **A smooth eased motion transition every `7 to 9 s`** (`4 to 6` transitions in a `35 to 45 s` reel) at scene shifts, hook-to-split entry (`tr=split_reveal@0.40` or `tr=warm_flash@0.38`), split-to-teaching exit (`tr=split_reveal@0.40` or `tr=whip_right@0.34`), and topic turns (`tr=whip_left@0.34`, `tr=whip_right@0.34`, `tr=slide_up@0.36`, `tr=zoom_burst@0.36`, `tr=flash_zoom@0.36`; `tr=dip_to_black@0.30` only for a real time jump). Keep rapid A/B turns inside an active split as crisp `tr=cut` swaps unless hitting a major scene/CTA shift.
- **Audible beat SFX (`-4 to 0 dB`) in full-frame AND splits:** punctuate key punch-ins, teaching callouts, reactions, reveals, and speaker-turn shifts (`pop.wav` at `-3 dB` on rapid punch-ins/callouts, `whoosh.wav` or `sn_swish_1.wav` at `-3 to -2 dB` on transitions and key turn shifts, `sn_punch_1.wav` at `-2 to -1 dB` on shock/peak beats, `sn_riser_1.wav` at `-3 to -2 dB` into big reveals, `click.wav` at `-3 dB` on prompt/UI beats). Never spam every turn — hit key reactions, reveals, and punch-ins.

## Sound effects (library `assets/sfx`; `sfx:<file>@<t>/<gain dB>`, `t` relative to the cut)
SFX assets are peak-normalized to `-1 dBFS` and mixed post-`loudnorm` so they stay crisp and audible on phone speakers: `# SFX:` / inline `sfx:` entries stay **`<= 0 dB` (`-4 to 0 dB`, typically `-3 to 0 dB`)**, while `# MARKS:` defaults are **`+1.0 dB` (`wrong`)** and **`+0.5 dB` (`right`)**. One effect per beat at most, never on a silent listener-only moment, and **always use `sn_riser_1.wav` for tension risers**.

| Need | File | Place it | Gain |
|---|---|---|---|
| Light reveal, key callout, or rapid zoom (`single` or `split`) | `pop.wav` | `at` = the zoom or keyword time (loudest at 0.01 s) | -3 |
| Impact on a shock or a peak | `sn_punch_1.wav` (hit at 0.15 s) or `sn_punch_2.wav` (0.16 s) | `at` = word start - 0.15 | -2 to -1 |
| Tension building to a reveal | `sn_riser_1.wav` (1.35 s, peak at 1.08 s) | `at` = reveal word start - 1.08; when the reveal sits `>= 1.1 s` into the cut | -3 to -2 |
| Success, praise (unmarked breakthrough) | `correct_ding.wav`, `ding.wav` (a ✓ mark already plays `correct_ding`) | on the praise/payoff word | -2 |
| A mistake (`✗` mark) | `sn_wrong.wav` (played automatically by `mark:wrong`) | end of the wrong sentence | `+1.0` (mark default) |
| A corrected answer (`✓` mark) | `correct_ding.wav` + `sn_correct.wav` (played automatically by `mark:right`) | end of the right sentence | `+0.5` (mark default) |
| Transition or key turn shift | `whoosh.wav`, `sn_swish_1.wav`, `w42_transition.wav` | the join (`at=0`) | -3 to -2 |
| Selection, prompt, or UI feel | `click.wav` | on the prompt/CTA word | -3 |

- The library has no laughter, gasp, heartbeat, sad sting or applause. For those moments use the footage's own sound, a `0.15 s` dip into silence before the beat, or tell the user the effect is missing. New licensed files go into `assets/sfx/` and into this table.
- A transition, a mark and an emotion effect on the same join are too much: keep the strongest.
- **Variety (editor feedback: every video sounds the same).** No sound effect file may be more than 40% of a plan's SFX and no transition more than 40% of its transitions; never the same transition twice in a row; use at least 3 different transitions in a reel of 35 s or more. Rotate `pop.wav`, `click.wav`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`; do not use `riser.wav` (broken, use `sn_riser_1.wav`). `--check-plan` prints a `[STYLE]` line when this is broken.
- **Flow clips (one clip holding a whole conversation):** cut in the clip's own order. `--check-plan` blocks `SOURCE ORDER` when a later script line is cut from earlier in the same clip, and cut every pause over 0.25 s and every gap at a join (`[TIGHT]` lines).
- Aim for clean, intentional punctuation (~1 audible SFX or mark every `5 to 8 s` across both live and split sections), hitting peaks, key zoom punch-ins, and transitions without spamming every line.

## Cuts (times from `facts.md`, seconds in the clip)
- **Do NOT double-pad word timestamps:** `facts.md` word bounds already include onset and vowel-decay guards, so add at most **`0.02 to 0.05 s` breath margin** (`start` `0.02 to 0.05 s` before the first kept word, `end` `0.03 to 0.05 s` after the last kept word — never `0.06 to 0.12 s` extra manual padding!). A cut inside a word clips it, and `--check-plan` blocks that.
- **Snappy joins & zero stretched monologues:** Tighten dialogue turn joins to **`0.06 to 0.15 s`** total gap (never `> 0.20 s` dead air between turns unless a dramatic script beat asks for it). Cut **every** internal dead pause **`> 0.25 s`** inside `single` monologues into tight segments (`L4a`, `L4b`) masked by an instant `z=1.08` punch-in. In a split there is no zoom to hide a cut: cut only pauses of `0.5 s` or longer, and advance `peer=` continuously across `L4a` -> `L4b` so the silent listener plays on without resetting. Use `speech gaps` on noisy clips, and treat words flagged `NO SPEECH` as ghosts (cut where `speech` spans end).
- **Tight `✗` / `✓` mark tails (`0.35 to 0.42 s`, `0.48 s` 2-pulse flash):** When a line carries a `✗` (`wrong`) or `✓` (`right`) mark, trigger `mark:wrong@<last word end - cut start - 0.06>/0.48@style=flash` or `mark:right@<last word end - cut start - 0.06>/0.48@style=flash` right as the final word finishes, and hold the cut tail for **only `0.35 to 0.42 s`** after `last_word_end` (never `0.70 to 1.00 s` of frozen actor stare!).
- **Zero unscripted repeated words or stumbles (intra-cut AND cross-boundary):** Check every `! REPEAT / STUMBLE` flag in `facts.md` (and `[REPEAT]` note in `line_map.md`) and trim out **every** false start, stutter, or repeated phrase — both inside a single cut and across adjacent cut boundaries (e.g., `L23b -> L24a` where one cut's tail and the next cut's head repeat the same words). For head/tail repeats, keep the clean saying and cut in the gap; when a stumble occurs **mid-sentence** with no clean full retake, split around the stumble (`L5a` + `L5b`) masked with a `z=1.08` punch-in! Scripted repeats and natural reduplications (e.g., Kannada ಮತ್ತೆ ಮತ್ತೆ) stay.
- Speech running past the script's last word is a babble to cut. A clip that opens with the word the previous cut ended on starts after that word. A note such as `(laughs mockingly)` keeps up to 1.2 s of the reaction after the last word.
- A silent beat (`(Man takes out his phone)`) uses a `SILENT` clip that shows it, chosen from the sheet, held only as long as the action needs.
- Choosing a take: the one that says the script's words completely with no stumble, then the clean clip end, then the later take. Two clips with different script lines are not takes of each other: a learner repeating the teacher's corrected sentence is a line of its own.
- Set `sp="<name>"` on every segment. The check measures the voice against it; `WRONG VOICE` means choose another take, and if no clip has the right voice, tell the user to reshoot.

## Layout
| Situation | Layout |
|---|---|
| One person talking: hook, teaching line, two people in one frame | `single` (default) |
| Pre-stitched / baked split in one clip (Google Flow) | `single` with `slot=top\|bottom\|left\|right` (or `active=top\|bottom\|left\|right`), `z=1` (no zoom) |
| 3D hologram in one frame | `single`, `z=1` |
| An ordinary two-shot of live actors (`shot_type=two_shot`, both in one scene) | `single`, may zoom up to `1.10` when `facts.md` gives safe `focus=x,y` |
| Two characters in separate clips | `split_h` (left/right) or `split_v` (top/bottom) |
| The script names a floating window, a split or a call | follow the script |
| A language drill "How to say X?" | `chat_bar` with `chat:` |
| A remote call | `video_call` |

Writers use "vertical" and "horizontal" split loosely, so the explicit words win: `Left:`/`Right:` means `split_h`, `Top:`/`Bottom:` means `split_v`. Only when a script gives no side words, "horizontal split" means top and bottom and "vertical split" means left and right.

**Split screens with four clips (the normal case):** the left talker, the right talker, and a silent clip for each character. A character is shown talking only while they speak; the other panel shows the other character's silent clip, so a talker never appears in the listener panel. Each character keeps one panel for the whole split (`Left: A | Right: B` in the script). Give each split segment a `peer=` window: a stretch of the other character's clip in which they are not speaking. It does not have to sit at the same time as the cut; use the quiet stretches (`speech gaps` in `facts.md`). Walk forward through it turn after turn, never restarting at the same moment. When a window is shorter than the cut the renderer loops it, and a loop under a second is invisible; choose the longest quiet stretch you can, and tell the user when a loop is long. A long two-camera recording where each person is silent outside their turn can be its own listener: use the other character's quiet stretches. When a character has nothing to show, tell the user.

**Stay in the script's split for all of its dialogue**, including long teacher blocks, the payoff and the closing call to action; do not break out to `single` just to get a zoom (the client reviews against the script; a split stays still at `z=1`). Keep both characters' baseline heads at matched height and scale (`face_top-bottom` in `facts.md`), telling the user if a clip cannot be matched.

    L8 Lady.mp4 0.02-3.40 layout=split_h z=1 sp="Lady" slot=right peer="Miss Nova:Nova_silent.mp4"@4.15-7.53@left

## Transitions, marks, text
- **Smooth eased physical transitions every `7 to 9 s`** (`4 to 6` transitions in a `35 to 45 s` reel) at scene shifts, hook-to-split entry, split-to-teaching exit, and topic turns:
  - Entering or exiting `split_h` / `split_v`: `tr=split_reveal@0.40` (opposing panel slide-in/out) or `tr=warm_flash@0.38` (or `tr=whip_right@0.34` on exit).
  - Scene shifts, clip changes & topic turns: `tr=whip_left@0.34`, `tr=whip_right@0.34` (alternate sides), `tr=slide_up@0.36`, `tr=zoom_burst@0.36`, `tr=flash_zoom@0.36` (and `tr=dip_to_black@0.30` only for a real time jump).
  - Inside a continuous split, keep fast A/B turn swaps as crisp `tr=cut` cuts. Kinetic transitions attach calibrated whoosh automatically (or specify `sfx:whoosh.wav@0/-2` / `sfx:sn_swish_1.wav@0/-3`).
- `✗`/`✓` only where the script marks a line (`mark=wrong`/`mark=right` in `line_map.md`), triggered right as the wrong or right sentence finishes (`at = last_word_end - cut_start - 0.06`) with **`0.48 s`** 2-pulse flash and **`0.35 to 0.42 s`** post-word tail (`end = last_word_end + 0.35..0.42 s`): `mark:wrong@<t>/0.48@pos=top-right@style=flash` (`red_flash.png` + `sn_wrong.wav` at `+1.0 dB`) or `mark:right@<t>/0.48@pos=top-right@style=flash` (`green_flash.png` + `correct_ding.wav` at `+0.5 dB`).
- A "Stop saying" hook: `banner:"header:Stop saying"@0.3`, then one banner per phrase on its spoken word (`banner:"What do you mean?"@<word start>`). The stack is placed below the measured face automatically.
- Strike-through lines (`(X Sign and Text Strikethrough)`): `banner:"<text>"@<time>` on the spoken word. Premiere-grade `#subs auto y=68` captions are ON by default; add no unrequested decorative B-roll, extra text banners, or spammy CTAs that the script or style did not ask for.

## Subtitles (ON by default: Premiere-grade animated `#subs auto y=68`)
`#subs auto y=68` is included **by default** in `plan.edl` (only write `#subs off` if the user explicitly asks for a clean/unsubtitled export!), and Zeno renders Premiere-grade animated word-pop karaoke captions as the final pass (saving `.srt` alongside the MP4). **Keep `auto` unless a specific preset is needed:** Zeno rotates creator presets so consecutive edits look fresh, adapts pill/outline contrast to the scene behind each cue, and automatically shifts `y=50` onto the `split_v` horizontal seam (so neither top nor bottom speaker's face is ever covered) and clears `banner:` stacks and low chins. Words render in exact spoken script (Kannada/Hindi/Tamil/Telugu/Malayalam + English) with active-word pop animation. To lock a specific preset from `subtitles/styles.json`, write `#subs "Docu Split-Bar" y=68` (and explain why in the plan summary; `adapt=off` keeps stored colours).

| Style | Look | Use it for |
|---|---|---|
| Hormozi Viral Pill / Blue Highlight | bold white text with thick black contour + electric active-word pill pop | high-retention Reels / Meta-ad default |
| Docu Split-Bar | dark card with vertical accent bar + active-word highlight | clean documentary / interview ads |
| Glass Grey / Cinema 35mm | dark rounded box or film-yellow outlined text with active highlight | busy backgrounds or cinematic story scenes |

Check `review_sheet.jpg` after rendering to confirm captions never cover a face or banner.

## plan.edl
`zeno.py --inspect` writes the header (including `#subs auto y=68`); you add one line per segment, in script order:

    <id> <clip file> <start>-<end> [layout=..] [z=..] [focus=x,y] [sp="Name"] [slot=..] [tr=name@sec] [peer=..] [mark:..] [banner:..] [sfx:file@sec/gain] [vol=+3dB]  # note

**A clip name that contains spaces goes in quotes**, as in `"MISS NOVA FV.mp4"`; an unquoted name makes the parser drop the whole line without a message, and the check then reports the line as unread. `id` is the script line (`L5`; `L5a`/`L5b` for pause-trimmed or stumble-trimmed parts; `L6_L7` for one cut holding two lines): the check reads the id and also compares the real words in the cut. Times inside `mark`, `banner`, `sfx` and `z=...@` are relative to the cut's start. Two lines from a real plan:

    L6a S1.mp4 0.58-9.78 z=sudden_in:1.08@0.25 focus=0.5481,0.2746 sp=Lady banner:"header:Stop saying"@0.3 banner:"What do you mean?"@1.06 banner:"I don't understand"@1.67 banner:"Repeat please"@2.89 sfx:pop.wav@0.25/-3  # hook: rapid zoom + pop on first word, phrases on their words
    L10 Lady.mp4 4.28-8.92 layout=split_h z=1 sp=Lady slot=right peer="Miss Nova:Nova.mp4"@4.55-9.19@left sfx:pop.wav@2.13/-3  # split peak: the picture stays still; the pop lands on "Wow"

## Checks, and what to do with them
- `--check-plan` measures and does not choose. It lists unread plan lines first, and blocks on lines out of script order, missing or said twice, unscripted repeated words/phrases (stumbles inside a cut or across adjacent cuts), stretched pauses/dead air, missing `#subs` (unless `#subs off` was requested), words in a cut that fit no line, cuts inside a word, and a voice that does not match `sp=`. Clear `[TIGHT]` and `[STYLE]` pacing/engagement warnings too before asking Yes/No rather than rationalizing them away; record genuine scripted exceptions in `work/<project>/decisions.txt` as `accept L2 <reason>`.
- After rendering, `verify` checks order, zero unscripted repeats, loudness, tight cuts/pauses, picture sync, black frames, caption/banner face clearance, and faces in every panel (`[FRAMES]` with the local face detector, no model tokens), and builds `review_sheet.jpg` (three small frames per cut). Read the sheet once; look at more frames only for a failure or a visual problem the user reports, and then only those moments.
- Then show the user the plan and ask the one Yes/No. `--execute` runs the same plan check again before it renders.
