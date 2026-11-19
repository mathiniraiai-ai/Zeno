# Core Editing Rules

## 1. Script Order & Speakers
- Play every spoken line once, in order.
- Verify words in `line_map.md` & `facts.md`.
- Set `sp="ExactSpeaker"`; report missing takes.
- Unspoken notes in `decisions.txt`: `accept L2 <why>`.

## 2. Word Bounds & Pauses
- Guard speech `0.02-0.05s`; join turns `0.06-0.15s`.
- Trim `! REPEAT / STUMBLE` & cross-join repeats.
- Keep scripted laughs/cries `<=1.2s`.
- `single` pause `>0.25s`: split (`L4a`/`L4b`) + `1.08` punch-in.
- Split pause `>=0.50s`: cut & advance `peer=`.

## 3. Split-Screen Invariants
- `Left:`/`Right:` = `split_h`; `Top:`/`Bottom:` = `split_v`.
- `split_h`, `split_v`, `top_pip`, baked splits: ALWAYS `z=1`.
- Fixed frame, matched bg, `14%` headroom.
- Advance silent `peer="Name:clip.mp4"@in-out@side`.
- Baked split: `single` + `slot=left|right|top|bottom z=1` (no re-split).

## 4. Full-Frame Zoom & Slo-Mo (`single`)
- `1.0` on calm cuts (`>=25%`), pans, edge 2-shots, holograms; reset cleanly to `z=1` on speaker/clip cuts.
- Smooth push/pull: `z=zoom_in[:1.08]` (or `1.0->1.06` on turns `>=2.5s`), `z=zoom_out[:1.10]` (smooth reveal pullback).
- Emphasis punches (`0.05s` early, Hook/Climax): `z=sudden_in:1.08@t` (3-5f silent punch-in, `<=16/min`, no repeat), `z=sudden_out:1.08@t` (recoil pullback).
- Action/slo-mo reveal: `z=bell:1.20->1.55->1.0` (bell curve); max 1 `flash_zoom:1.12@t`; pass `focus=x,y` from `facts.md`.
- Slo-Mo Hook: when script calls for a slo-mo opening hook before live dialogue, use `speed=0.45 mute` (or `speed=0.5 mute beat`) + `z=bell:1.20->1.55->1.0` + whoosh SFX.

## 5. Story-Only Transitions
- Add `+1-2` tasteful beats per reel on real shifts; keep majority of dialogue cuts as crisp hard cuts; zero intra-split, same-shot, or mid-line transitions.
- Scene switches & story->CTA: `tr=whip_left`/`whip_right` (`0.34s`), `tr=slide_up`/`slide_down` (`0.36s`), `tr=zoom_burst` (`0.36s`; aliases `whip_up`, `whip_down`, `smooth*`).
- Time/location jumps: `tr=dip_to_black`, `tr=dissolve`, `tr=fade` (`0.30s`); hook/emphasis/split entry: `tr=zoom_in`, `tr=zoom_out`, `tr=zoom_blur`, `tr=split_reveal@0.40`.
- Act pivot / seamless AI stitch mask (flashes, max 1): `tr=warm_flash_punch` (`0.34s`), `warm_flash` (`0.375s`), `dip_to_white` (`0.30s`), `flash_zoom` (`0.36s`). Kinetic/flash transitions auto-attach calibrated whoosh SFX.

## 6. Marks, Audio & Calibrated SFX
- Audio: zero continuous BGM by default (clean dry/diegetic dialogue + surgical SFX only, mastered to `-10.5 LUFS`).
- Packs: `#mark_pack flash|vignette|default|glossy|supernova|kn20228|w42`; `#sfx_pack default|supernova|kn20228|w42`.
- Scripted `wrong`/`strike_text` & `right` lines auto-attach post-word `0.48s` outer vignette + strip slash + SFX & hold tail (manual `mark:wrong|right@t/0.48`, `hold=0.42` optional):
  - Wrong (`+1dB`): `sn_wrong.wav`, `buzzer.wav`, `w42_wrong.wav`, `kn20228_wrong_core.mp3`.
  - Right (`+0.5dB`): `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav`, `kn20228_correct.mp3`, `ding.wav`.
- SFX (`-4..0dB`, 1/beat, `<=40%`/file): UI `pop.wav`, `click.wav`, `sn_click.mp3`, `kn20228_click.wav`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`; Whoosh `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_swoosh.wav`.

## 7. Subtitles & CTA Safety
- `#subs auto y=68` (`54px` 1-row at `1080w`; `y=50` on `split_v`). `#page scroll` replaces `#subs`, banners, marks, & SFX.
- Flashcards: scripted `Flashcard Text:` auto-attaches `card:"English | Native"` (face-safe; never `banner:`, which is Stop-Saying strikethrough; opt out via `card:none`/`#cards off`).
- 1:1 Square (`--square`): always derived directly from the completed 9:16 reel (2 layers: 1:1 center-cropped ~7% [5-10%] Gaussian blur background + centered full 9:16 reel on top; never generate separate 1:1 images, cards, timestamps, or subtitles).
- Never add burned-in fake `"Install Now"` CTA buttons; keep bottom `20-25%` clean for platform ad buttons.

## 8. `script_analysis.md` & `plan.edl`
- `script_analysis.md` L1: `Format: <id> (<title>)`, 1 row/line.
- `plan.edl` (`t` from start):
  `L8 "A.mp4" 0.02-3.40 layout=split_h z=1 sp="A" slot=right peer="B:idle.mp4"@4.15-7.53@left`
  `[focus=x,y] [speed=0.45 mute] [tr=k@d] [mark:wrong@t/0.48@style=flash] [banner:"x"@t] [card:"En | Nat"@pos=auto] [sfx:pop.wav@t/-3]`
