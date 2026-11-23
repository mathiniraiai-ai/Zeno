# Format: skit — 1.1 Skit-Based Micro-Narrative

Header line 1 of `script_analysis.md`: `Format: skit (1.1 Skit-Based Micro-Narrative)`

## Recognition & Story Arc
- **Script cues**: `Scene N` headings, `Visual:` block, character list, parenthetical tone notes (`(mockingly)`), `24-40` spoken lines (`60-130s`).
- **Four-beat arc**:
  1. **Live Problem / Hook (Scene 1)**: Public English struggle or humiliation across multiple human actors; snappy speaker-follow cuts (`0.06-0.15s` joins) and reaction beats.
  2. **Bridge / Tip (Scene 2)**: Friend, coworker, or phone partner relates to the problem and introduces the AI teacher (`"try this"`), or triggers a phone hologram pop-out.
  3. **AI Teacher Drill (Scene 3)**: Learner practices with Miss Nova / Female Robot; wrong attempt (`mark:wrong`, when scripted) is corrected and praised (`mark:right`, when scripted).
  4. **Closing CTA**: `"Install Now"` / Supernova AI call to action.

## Four Raw Footage Modes (Inspect Clip Sheets Before Layout Assignment)
1. **Separate HeyGen / Single-Person Clips (`split_h` or `split_v`)**:
   - Two characters arrive as separate single-person `.mp4` files (ideally 2 talking + 2 silent listener clips).
   - Composite with `layout=split_h` (`Left:`/`Right:`) or `layout=split_v` (`Top:`/`Bottom:`), `z=1` always (`14%` headroom).
   - Pair active speaker with live silent listener `peer="Name:clip.mp4"@in-out@side`, advancing across turns and `>=0.55s` dead-air pauses (keep sentences complete `start - 0.05s`..`end + 0.06-0.10s` via `pauses:`; never cut `<0.45s` breaths).
2. **Google Flow Pre-Stitched Split (`layout=single` + `slot=`)**:
   - Raw `.mp4` already has both panels stitched inside one `9:16` frame (`720x1280` or `1080x1920`).
   - Never apply `split_h`/`split_v` or `peer=` (prevents double-cropping). Cut sequentially with `layout=single slot=left|right|top|bottom z=1` and `0.06-0.15s` turn joins.
3. **In-Frame Two-Shot & 3D Hologram (`layout=single`)**:
   - Characters share one `9:16` frame or Miss Nova's 3D hologram rises from a phone in-frame with the learner.
   - Keep `layout=single` throughout. Lock `z=1.0` on all hologram shots and edge-framed two-shots; safe live shots may zoom up to `1.08-1.10` with `focus=x,y`. Preserve physical action beats and hologram pop-out tails.
4. **Top Floating Window (`layout=top_pip` + `<stem>VO.mp4` Mux)**:
   - Full-screen `1080x1920` background shows the human learner (`M1`/`M2`, or `M extra.mp4` silent listener) while a top-center `750x420` rounded window (`x=165, y=20, r=36px`) shows the AI Robot (`R1`..`R4`, or `R extra.mp4` silent listener), `z=1`.
   - When `<stem>VO.mp4` accompanies `<stem>.mp4`, mux `<stem>.mp4` video (`0:v:0`) with `<stem>VO.mp4` partner audio (`1:a:0`) before cutting.
   - Interleave multi-turn clips cleanly (`0.06-0.15s` joins), trim `! REPEAT / STUMBLE` restarts, and add `banner:"..."` (auto-staggers without `@t`) on phrase cutaways.

## W47 Approved Edit Pattern
- **4-Act Pacing**:
  1. **Hook (`1.8-3.5s` beats)**: Opening `z=zoom_out[:1.10]` reveal or `speed=0.45 mute` slo-mo + `z=bell:1.20->1.55->1.0` + whoosh hook (when scripted), plus `1-2` crisp `z=sudden_in:1.08@t` punches; hold tense 2-plane eavesdropping shots static `z=1`.
  2. **Act 2 Body (`3.5-5s` beats)**: Calm slow `z=zoom_in[:1.08]` Ken Burns push-ins resetting cleanly to `z=1` at speaker/clip cuts; zero chaotic snaps.
  3. **Act 3 Climax (`1.5-2.5s` beats)**: Fast `z=sudden_in:1.08@t` punches (`sudden_out` optional) + `1` `tr=warm_flash_punch`/boom pivot.
  4. **Act 4 CTA**: Calm `z=1` lock-off or smooth phone pan+zoom; zero burned-in CTA buttons (keep bottom `20-25%` clean).
- **Diegetic Framing**: Keep phone-on-stand AI teacher shots as `layout=single` (don't force split screen unless script asks for split screen).
- **Transitions (`+1-2` tasteful beats)**: `tr=dip_to_black` on act/time jumps, `tr=slide_up`/`whip_left`/`whip_right` into CTA or new scenes, `tr=zoom_in`/`zoom_out`/`warm_flash_punch` on pivots/AI seams; hard cuts for most dialogue turns.
- **Feedback & Audio**: Optional single green outer vignette (`mark:right`) + chime on `"Perfect."` drill success with live non-frozen visuals during post-word marks/SFX without dialogue overlap; zero BGM (dry/diegetic dialogue + surgical SFX only).

## Zoom, Transitions, Marks & Subtitles
- **Zoom**: Emotion zooms in live full-frame scenes (`z=zoom_in[:1.08]` / `1.0->1.06` on turns `>=2.5s`, `z=zoom_out[:1.10]` reveal, `sudden_in:1.08@t` / `sudden_out:1.08@t` without slicing audio, `1.08` punch-in only on `>=0.45s` `pauses:`, `bell:1.20->1.55->1.0` slo-mo hooks, max 1 `flash_zoom:1.12@t`, `>=25%` `1.0` cuts); splits & holograms stay `z=1`.
- **Transitions** (`max_transitions: null`, story-driven only):
  - Allowed: `whip_left`, `whip_right`, `slide_up`, `slide_down`, `split_reveal`, `dip_to_black`, `dissolve`, `fade`, `zoom_burst`, `zoom_in`, `zoom_out`, `zoom_blur`, `warm_flash_punch` (plus aliases `whip_up`, `whip_down`, `smoothleft`, `smoothright`, `smoothup`, `smoothdown`).
  - Script-cued flashes (max 1): `warm_flash`, `warm_flash_punch`, `dip_to_white`, `flash_zoom` (`warm_flash` also pairs with `split_reveal` when entering/leaving a split).
  - One on each real scene/location change, `tr=split_reveal@0.40` entering the split, at most one leaving to a standalone CTA. Hard cuts everywhere inside a scene or split.
- **Marks & SFX**: Scripted `wrong`/`strike_text` & `right` lines auto-attach post-word `0.48s` outer vignette (+ strip slash on `strike_text`) + SFX over live non-frozen video visuals without dialogue overlap (manual `mark:` optional; never freeze frames). Beat & transition SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `kn20228_click.wav`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_swoosh.wav`) at `-4 to 0 dB` (1/beat, `<=40%`/file).
- **Subtitles**: `#subs auto y=65` (strict single-line only, `54px`; auto-centers to `y=50` on `split_v` seam).
