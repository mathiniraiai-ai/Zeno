# Format: street_interview — 1.3 Street Interview Micro-Narrative

Header line 1 of `script_analysis.md`: `Format: street_interview (1.3 Street Interview Micro-Narrative)`

## Recognition & Story Arc
- **Script cues**: Reference link, `Characters`, `Scene 1 / 2` headings, `20-30` spoken lines, street vox-pop / handheld microphone staging.
- **Four-beat arc**:
  1. **Street Vox-Pop Hook (Scene 1a)**: Handheld street-and-microphone exchange with an unexpected interviewee (e.g., elderly job seeker, bus driver, couple abroad) who answers surprisingly fluently in English.
  2. **Reveal & Bridge (Scene 1b)**: Interviewer asks how they learned; interviewee credits their AI teacher and bridges with `"let me show you"`.
  3. **AI Teacher Split Drill (Scene 2)**: Two-panel practice demo with Miss Nova / Female Robot correcting and praising a real-life phrase.
  4. **Closing CTA**: `"Install Now"` / Supernova AI call to action (inside the split or back on the street).

## Layouts, Handheld Zoom & Split Rules
- **Scene 1 Street Interview (`layout=single`)**:
  - Snappy hard-cut dialogue keeping every spoken sentence complete (`start - 0.05s` through `end + 0.06-0.10s`, verified against `pauses:` in `editing_packet.md`; `0.06-0.15s` turn joins) in `selfie` or `close_up` framing; trim all `! REPEAT / STUMBLE` false starts.
  - **Handheld Camera Stability Rule**: Check camera motion in `editing_packet.md` before adding zoom. Apply `z=sudden_in:1.08@t` (on the surprise fluent-English reveal) or `1.0->1.06` push (`focus=x,y`) ONLY where the camera is steady; lock `z=1.0` on shaky handheld clips, camera pans, or edge-framed two-shots.
  - Never cut `<0.45s` mid-sentence breaths or commas; trim only genuine dead-air pauses `>=0.50s`, and on steady shots either punch in (`1.08`) at a real `>=0.45s` pause in `pauses:` or use `z=sudden_in:1.08@t` inside the continuous cut without slicing the audio.
- **Scene 2 Split Drill (`layout=split_h` or `split_v`)**:
  - Follow explicit `Left:`/`Right:` (`split_h`) or `Top:`/`Bottom:` (`split_v`) panel assignments. If pre-stitched Google Flow clips are supplied, use `layout=single slot=left|right|top|bottom z=1`.
  - Every split cut is strictly `z=1` (`14%` headroom, fixed panel framing) with a dedicated silent listener `peer="Name:clip.mp4"@in-out@side` advancing continuously across turns (`>=0.55s` internal dead-air pauses trimmed).
  - Hold the split through the CTA unless footage includes a separate closing street shot.

## Transitions (Max 2)
- **Allowed kinds**: `split_reveal`, `whip_left`, `whip_right`, `slide_up`, `slide_down` (plus script-cued `warm_flash`, `warm_flash_punch`, `dip_to_white`, `flash_zoom` at most once when requested).
- **Placement**:
  - Hard cuts (`tr=cut`) across every question-and-answer turn in the street interview and across every turn inside the split.
  - `1` transition (`tr=split_reveal@0.40`, or `whip_left`/`whip_right`) at the `"let me show you"` bridge entering the split.
  - At most `1` transition exiting the split to a separate street CTA shot (`2` total max).

## Marks, SFX & Subtitles
- **Marks**: Trigger `mark:wrong@t/0.48@style=flash` (`sn_wrong.wav`/`buzzer.wav`/`w42_wrong.wav` `+1dB`) and `mark:right@t/0.48@style=flash` (`correct_ding.wav`/`sn_correct.wav`/`w42_right.wav` `+0.5dB`) post-word over live non-frozen video visuals without dialogue overlap strictly where marked in the script.
- **SFX**: Punctuate the surprise fluent answer, transitions, and drill reveals with varied `-4 to 0 dB` SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_*`, max 1/beat, never on silent listener).
- **Subtitles**: `#subs auto y=65` (strict single-line only, `54px`; auto-centers to `y=50` on `split_v` divider).
