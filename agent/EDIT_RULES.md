# Zeno editing rules

The generated `brief.md` selects the applicable sections below. Read that brief once per edit. Read a referenced topic only when the job needs its detail.

## Craft principles
- The script defines the timeline. Every spoken line appears once, in order. Choose complete, fluent takes by speech and picture. Do not trust file order or take names.
- The agent decides takes, cuts, layouts and effects. Python measures and checks them.
- Keep faces clear, framing consistent and pacing conversational. Add only script/style-requested graphics, marks and CTAs.
- When footage cannot deliver a line, voice or action, report it. Do not invent missing content.

## Reading the measurements
- Read `line_map.md`, `facts.md` and all clip sheets completely. CAND lists alternatives, not decisions. `heard` measures word coverage.
- In facts, `1.20 word` is the word start; `|2.31` is a phrase end. Use speech spans over noisy loudness measurements. Trim every unscripted `! REPEAT / STUMBLE`, including repeated words across cut joins. `NO SPEECH` and `TIMING WRONG` require inspection of that boundary.
- Use clip sheets to identify people, framing, baked splits, actions and silent clips. A `looks like` match is a hint requiring visual confirmation.
- Set `sp="Name"` on every cut using the script's exact speaker label. A wrong voice requires another take or a reshoot. Disclose `voices not checked`.
- Unspoken script directions become layout/action/banner choices. If the checker treats one as dialogue, explain it in `decisions.txt` as `accept L2 <reason>`.

## Emotion plan
Write `script_analysis.md` before the plan. First line `Format: <id> (<name>)`. One short row per script line with speaker, emotion/intensity (1–3), beat, motion, sound and reason. Match emotion to script, voice and picture. Strong reactions and reveals deserve emphasis, while quieter lines give them contrast. Splits stay at `z=1`.

## Cuts
- Word bounds already contain acoustic guards. Add only 0.02–0.05 s before and 0.03–0.05 s after speech. Target 0.06–0.15 s total silence at dialogue joins.
- Cut internal pauses longer than 0.25 s in full-frame monologues, masking the join with a 1.08 step punch-in. In still splits cut pauses of 0.5 s or longer, preserving shorter natural breaths. Advance the silent listener across those cuts.
- Trim false starts, repeated words and trailing babble. Keep scripted repetitions. Preserve a scripted laugh/cry up to 1.2 s and intentional silent action only as long as needed.
- A marked sentence gets a 0.35–0.42 s tail. Other turns stay tight.
- A multi-turn clip follows its source order. Verify every chosen window against the words, including the first and last word of each line.

## Layout
- Follow explicit positions in the script. Left/right means `split_h`; top/bottom means `split_v`. Without positions, “horizontal split” means top/bottom. A baked split uses `single` with `slot=`; never composite it twice.
- Every split, including baked splits, stays at `z=1`. No push, pull or punch-in. Keep one fixed frame per character, matched backgrounds and 14% headroom.
- The speaking character occupies their assigned panel; the other character is silent. Use that character's dedicated listener clip, walking forward across turns. Otherwise use a quiet mouth-closed stretch, held if shorter than one second. Disclose fallback, visible loops or missing listeners.
- Keep the split through its dialogue and CTA. Do not switch layouts just for visual variety. Use measured `focus=x,y` for full-frame crops.

## Zoom (full-frame only)
- Keep neutral short beats at 1.0. For longer confident/teaching turns use 1→1.06; for emphasis use `sudden_in:1.08@t`; release with `sudden_out:1.08@t` or 1.06→1. A strong reveal may use `flash_zoom:1.12@t`.
- Start an emphasis zoom 0.05 s before the stressed word. Use a slow push for tension, a slow pull for relief. Do not zoom deliberate pans/dollies or 3D holograms. A shared two-shot may zoom only within its measured safe framing.
- For detailed motion choices see `agent/REFERENCE.md` only if needed. Splits never zoom.

## Transitions, marks and sound
- A transition marks a new place/time, entering/leaving a split, or a separate CTA shot. Hard-cut continuous dialogue and same-shot edits. No transitions within splits or within one line.
- New place: `whip_left`, `whip_right` (0.34 s) or `slide_up` (0.36 s). Split entry/exit: `split_reveal@0.40`. Time jump: `dip_to_black`; flashback: `dissolve`. White/warm flashes and flash-zoom transitions require a script cue, once at most.
- Marks only on script-designated wrong/right lines. Default mark pack `flash`: two pulses over 0.48 s, beginning `last_word_end - cut_start - 0.06`, with the 0.35–0.42 s tail. Wrong sound `sn_wrong.wav` +1 dB; right `correct_ding.wav` +0.5 dB.
- Punctuate selected emotional/teaching beats. SFX gains -4 to 0 dB, usually -3: `pop.wav`, `click.wav`, `sn_punch_1.wav`, `sn_swish_1.wav`. Use `sn_riser_1.wav` for tension. One effect per beat, no effect on a silent listener, no one file over 40% of SFX when several are used.
- “Stop saying” hooks may use the header and phrase banners. Render script-requested strike-through phrases. Additional layout, mark pack and SFX syntax is in `agent/REFERENCE.md` when required.

## Scrolling-text page
- `#page scroll`: two characters side by side in the top half, blue rule below, scrolling white page in the bottom half. A box highlights the spoken word. Pictures stay still throughout.
- Every cut is `layout=split_h z=1`, with `slot=` and the silent `peer=`. Hard cuts only. The page supplies the text; omit `#subs`, banners, extra graphics, marks and sound effects.
- The page derives the words and their times from the transcript. One paragraph per speaker turn. Inspect the rendered page for spelling and highlight timing. Report transcript defects for correction.
- Default page size is 44 at 1080-wide output. Optional `bar`, `dash` and `fade` stay off unless requested. Wrong/right phrase colours require the user's request.

## Subtitles
- Default `#subs auto y=68`. One fixed style and size for the whole video, 54 px at 1080 width. Always a single row. No per-caption colour changes unless `adapt=on` is requested in the plan.
- Full-frame captions sit at 65–70% height. Top/bottom split captions always sit on the divider at y=50. Keep text off faces and banners. `#subs off` only when requested. Scrolling-page format supplies its own text.

## plan.edl
Keep the generated output, source, script and canvas headers. Add one line per cut:

    L8 "Lady.mp4" 0.02-3.40 layout=split_h z=1 sp="Lady" slot=right peer="Miss Nova:Nova_silent.mp4"@4.15-7.53@left

Format: `<id> <clip> <start>-<end> layout=... z=... sp="Name" [focus=x,y] [slot=...] [peer=...] [tr=name@duration]`.
IDs match script lines; use `L4a`, `L4b` for parts. Quote filenames containing spaces. Extra forms are `mark:wrong@t/0.48@style=flash`, `banner:"text"@t`, `sfx:pop.wav@t/-3`, `vol=+3dB`. Effect times are relative to the cut start.

## Checks
Run `--check-plan` after writing or changing the plan. Fix blockers, clipped words, stumbles, missing/wrong speakers and unexplained pacing/style warnings. Record genuine scripted exceptions in `decisions.txt`.
Show the concrete plan and any footage limitations, then obtain the one pre-render approval. After approval run `--execute --square`. Read verification and review sheets, repair failures, and render again as needed. Verify the square export's framing too. Report uncertainties and token usage.
