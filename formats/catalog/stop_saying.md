# Format: stop_saying — 1.4 Stop Saying / Never Say Micro-Narrative

Header line 1 of `script_analysis.md`: `Format: stop_saying (1.4 Stop Saying / Never Say Micro-Narrative)`

## Recognition & Story Arc
- **Script cues**: Reference link, `Characters`, `Scene 1`-`5`, `16-24` lines (`79-101s`), `"Stop saying X"` / `"Never say X"` or struck-out / red-X phrases.
- **Five-beat arc**:
  1. **Struck Hook (S1)**: 2-3 textbook phrases with red strikethrough / X + wrong buzzer.
  2. **Bridge (S2)**: Presenter explains the upgrade and introduces the AI teacher.
  3. **AI Split Drill (S3)**: Two-panel practice with Miss Nova / Female Robot ending with a green right mark.
  4. **Return (S4, optional)**: Cut back to Scene 1 only if scripted; never force a return when script stays in split screen through CTA.
  5. **CTA (S5)**: Direct `"Install Now"` / Supernova AI call to action (no burned-in fake buttons; bottom `20-25%` clean).

## Layouts, Stacked Banners & Split Rules
- **Hook & Struck Phrases (`layout=single`)**:
  - Preserve calibrated post-word `0.48s` beat + ~30%-reduced hollow outer vignette (`#mark_pack vignette`, live non-frozen visuals, no dialogue overlap): scripted `wrong`/`strike_text` lines auto-attach outer vignette + strip slash + buzzer (`sn_wrong.wav`) over live non-frozen video (manual `mark:wrong@t/0.48@style=vignette` optional; never freeze).
  - On multi-phrase single-cut hooks, stack `banner:"Phrase 1"` (auto-staggers without `@t`, or pass `@t` for exact onset); keep `z=1.0` across banner stacks and moving shots.
- **Bridge, Return & CTA (`layout=single`)**:
  - Steady presenter cuts may use `z=zoom_in[:1.08]` (`1.0->1.06`) push or `sudden_in:1.08@t` (`focus=x,y`); split steady pauses `>0.25s` with `1.08` step punch-in (`z=1.0` if camera moves).
- **Split Drill (`layout=split_h` or `split_v`)**:
  - Use `split_h` (`Left:`/`Right:`) or `split_v` (`Top:`/`Bottom:`) at `z=1` (`14%` headroom), advancing `peer="Name:clip.mp4"@in-out@side` across turns (`layout=single slot=... z=1` if pre-stitched).
  - Proactively add green outer glow (`mark:right@t/0.48` + ding) on correct drill answers (and red glow + boom on split wrong examples) over live non-frozen visuals without dialogue overlap even when script omits `✅`/`❌`; zero BGM (dry dialogue + surgical SFX only).

## Transitions (Max 5)
- **Allowed**: `split_reveal`, `whip_left`, `whip_right`, `slide_up`, `slide_down`, `zoom_in`, `zoom_out`, `zoom_blur`, `dip_to_black`, `warm_flash_punch` (plus script-cued `warm_flash`, `dip_to_white`, `flash_zoom`, max 1 when requested).
- **Placement**: Allow `tr=zoom_out`/`zoom_in` across rapid wrong-phrase flashback beats, `tr=zoom_blur` or `tr=split_reveal@0.40` entering `split_v`/`split_h`, and optional `whip_*`/`slide_up`/`dip_to_black` on real location/return shifts (`5` total max; `0` inside split drill).

## SFX & Subtitles
- **SFX**: `sn_wrong.wav`/`buzzer.wav`/`w42_wrong.wav` (`+1dB`) & `correct_ding.wav`/`sn_correct.wav`/`w42_right.wav` (`+0.5dB`) auto-pair with marks; use `pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `kn20228_*` (`-4 to -1 dB`, `<=40%`/file) sparingly on bridge/CTA/transitions.
- **Subtitles**: `#subs auto y=65` (strict single-line only, `54px`, clear of banners; `y=50` on `split_v`).
