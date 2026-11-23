# Core Editing Rules

## 1. Script Order & Speakers
- Play every spoken line once, in order; verify words in `line_map.md` & `facts.md`.
- Set `sp="ExactSpeaker"`; report missing takes; unspoken notes in `decisions.txt`: `accept L2 <why>`.

## 2. Word Bounds, Sentence Integrity & Pauses
- Keep every sentence complete (`start - 0.05s` to `end + 0.06-0.10s`, checked via `pauses:`); join turns `0.06-0.15s`.
- NEVER cut `<0.45s` mid-sentence breaths/commas; trim `! REPEAT / STUMBLE` & keep scripted laughs/cries `<=1.2s`.
- Trim only dead-air pauses `>=0.50s` (`>=0.55s` inside `split_h`/`split_v`, advancing `peer=`).
- `single` `1.08` punch-in: use `z=sudden_in:1.08@t` inside the continuous cut or cut only at a `>=0.45s` `pauses:` gap.

## 3. Split-Screen Invariants
- `Left:`/`Right:` = `split_h`; `Top:`/`Bottom:` = `split_v`; `split_h`, `split_v`, `top_pip`, baked splits: ALWAYS `z=1` (`14%` headroom).
- Advance silent `peer="Name:clip.mp4"@in-out@side`; baked split: `single` + `slot=left|right|top|bottom z=1`.

## 4. Full-Frame Zoom & Slo-Mo (`single`)
- `1.0` on calm cuts (`>=25%`), pans, edge 2-shots, holograms; reset to `z=1` on speaker/clip cuts; pass `focus=x,y`.
- Push/pull: `z=zoom_in[:1.08]` (`1.0->1.06` on turns `>=2.5s`), `z=zoom_out[:1.10]`.
- Punches (`0.05s` early): `z=sudden_in:1.08@t` (`<=16/min`, no repeat), `z=sudden_out:1.08@t`; max 1 `flash_zoom:1.12@t`.
- Slo-Mo Hook: `speed=0.45 mute` (or `speed=0.5 mute beat`) + `z=bell:1.20->1.55->1.0` + whoosh SFX.

## 5. Story-Only Transitions
- `+1-2` beats/reel on real shifts; dialogue hard-cut; zero intra-split, same-shot, or mid-line transitions.
- Scene/CTA: `whip_left`/`whip_right` (`0.34s`), `slide_up`/`slide_down` (`0.36s`), `zoom_burst` (`0.36s`); time: `dip_to_black`, `dissolve`, `fade` (`0.30s`); hook/split: `zoom_in`, `zoom_out`, `zoom_blur`, `split_reveal@0.40`.
- Act/flash (max 1): `warm_flash_punch` (`0.34s`), `warm_flash` (`0.375s`), `dip_to_white` (`0.30s`), `flash_zoom` (`0.36s`) (auto whoosh SFX).

## 6. Marks, Audio & Calibrated SFX
- Audio: zero continuous BGM by default (dry dialogue + surgical SFX, `-10.5 LUFS`).
- Packs: `#mark_pack flash|vignette|default|glossy|supernova|kn20228|w42`; `#sfx_pack default|supernova|kn20228|w42`.
- Scripted `wrong`/`strike_text` & `right` auto-attach post-word `0.48s` vignette + slash + SFX over live non-frozen video without dialogue overlap (`mark:wrong|right@t/0.48`, never freeze):
  - Wrong (`-1.5dB`): `sn_wrong.wav`, `buzzer.wav`, `w42_wrong.wav`, `kn20228_wrong_core.mp3`.
  - Right (`+0.5dB`): `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav`, `kn20228_correct.mp3`, `ding.wav`.
- SFX (`-4..0dB`, 1/beat, `<=40%`/file): UI `pop.wav`, `click.wav`, `sn_click.mp3`, `kn20228_click.wav`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`; Whoosh `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_swoosh.wav`.

## 7. Subtitles & CTA Safety
- `#subs auto y=65` (strict single-line, `54px` at `1080w`; `y=50` on `split_v`). `#page scroll` replaces `#subs`, banners, marks, & SFX.
- `Flashcard Text:` auto-attaches `card:"English | Native"` (face-safe; never `banner:`; opt out `card:none`/`#cards off`).
- `--square` 1:1: 2-layer ~7% (`5-10%`) Gaussian blur wrap of the 9:16 reel (never separate 1:1 cards/subs).
- Never add fake `"Install Now"` buttons; keep bottom `20-25%` clean.

## 8. `script_analysis.md` & `plan.edl`
- `script_analysis.md` L1: `Format: <id> (<title>)`, 1 row/line.
- `plan.edl` (`t` from start):
  `L8 "A.mp4" 0.02-3.40 layout=split_h z=1 sp="A" slot=right peer="B:idle.mp4"@4.15-7.53@left`
  `[focus=x,y] [speed=0.45 mute] [tr=k@d] [mark:wrong@t/0.48@style=flash] [banner:"x"@t] [card:"En | Nat"@pos=auto] [sfx:pop.wav@t/-3]`
