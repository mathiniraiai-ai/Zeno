# Format: direct_hook — 1.2 Direct Hook Micro-Narrative

Header line 1 of `script_analysis.md`: `Format: direct_hook (1.2 Direct Hook Micro-Narrative)`

## Recognition & Story Arc
- **Script cues**: `Concept reference`, `Visual:`, `Characters:`, `Scene 1 / 2 / 3` headings, `20-25` spoken lines.
- **Three-beat arc**:
  1. **Direct Selfie Hook (Scene 1)**: Live-action or 3D presenter speaks straight to camera in selfie framing, calling out a common English pain point and promising a quick fix.
  2. **AI Teacher Split Drill (Scene 2)**: Two-panel conversation (typically AI teacher on the left, human learner on the right) practicing and correcting the target sentence.
  3. **Call to Action (Scene 3)**: Closing `"Install Now"` / Supernova AI prompt, either continuing inside the split or cutting to a dedicated closing selfie shot.

## Layouts & Footage Handling
- **Scene 1 Hook (`layout=single`)**:
  - Full-frame selfie cut with `0.02-0.05s` word guards and `0.06-0.15s` joins; trim every `! REPEAT / STUMBLE` false start.
  - Open with kinetic hook energy: use opening `z=zoom_out[:1.10]` reveal or `z=sudden_in:1.08@t` hook punch (`0.05s` before the first stressed hook word), and smooth `z=zoom_in[:1.08]` (`1.0->1.06`) push on setup sentences (`>=2.5s`) with measured `focus=x,y`, resetting cleanly to `z=1` on cuts.
  - Split internal monologue pauses `>0.25s` into `L1a`/`L1b` masked with a `1.08` step punch-in; zero continuous BGM by default.
- **Scene 2 Split Drill (`layout=split_h` or `split_v`)**:
  - Default `layout=split_h` (teacher `left`, human `right`), or `layout=split_v` when the script explicitly specifies `Top:`/`Bottom:`.
  - If raw footage is a pre-stitched Google Flow split, use `layout=single slot=left|right z=1` (never double-composite).
  - Lock `z=1` on every split segment (`14%` headroom, matched fixed framing). Supply a continuous silent listener `peer="Name:clip.mp4"@in-out@side` advancing across turns (`>=0.50s` internal pauses cut).
- **Scene 3 CTA**:
  - Stay in the split through the CTA unless a separate full-frame CTA clip exists (`layout=single`, calm `z=1` or subtle `z=zoom_in[:1.06]`); never burn in fake CTA buttons (keep bottom `20-25%` clean).

## Transitions (Max 4)
- **Allowed kinds**: `split_reveal`, `whip_left`, `whip_right`, `slide_up`, `slide_down`, `zoom_in`, `zoom_out`, `zoom_blur`, `dip_to_black`, `warm_flash_punch` (plus script-cued `warm_flash`, `dip_to_white`, `flash_zoom` at most once when requested).
- **Placement**:
  - Up to `4` tasteful transitions total across hook pivots (`zoom_in`, `zoom_out`, `whip_*`, `dip_to_black`), split entry (`tr=split_reveal@0.40` or `tr=zoom_blur`), and exiting to a standalone full-frame CTA (`slide_up`/`whip_*`).
  - Keep majority of dialogue cuts as crisp hard cuts; `0` transitions inside the split drill.

## Marks, SFX & Subtitles
- **Marks**: Scripted `wrong`/`strike_text` & `right` lines auto-attach post-word `0.48s` outer vignette (+ strip slash on `strike_text`) + calibrated SFX & hold tail (manual `mark:wrong|right@t/0.48@style=flash` / `hold=` optional).
- **SFX**: Accent the opening hook punch, transitions, and drill reveals with varied `-4 to 0 dB` SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_*`, max 1/beat, never on silent listener).
- **Subtitles**: `#subs auto y=68` (`54px` single row; auto-centers to `y=50` if `split_v` is used).
