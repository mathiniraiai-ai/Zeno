# Format 8: 3D to 2D (`3d_to_2d`)

Declare on line 1 of `script_analysis.md`: `Format: <subformat> (3D to 2D)`

## Generation Wrapper Rule
- `3d_to_2d` describes how the source footage was generated (a 3D story restyled or rendered as 2D animation), **not** an independent editing format.
- Always write the script's narrative sub-format ID first on line 1 of `script_analysis.md` — such as `Format: skit (3D to 2D)`, `Format: direct_hook (3D to 2D)`, or `Format: stop_saying (3D to 2D)` — so `--check-plan` validates layouts, zooms, and transition caps against the real story structure.

## Mapping Script Structure to Sub-Format
- Multi-scene incident + AI split lesson -> `Format: skit (3D to 2D)`
- Solo talking-head hook + AI split lesson -> `Format: direct_hook (3D to 2D)`
- Handheld mic vox-pop hook + AI split -> `Format: street_interview (3D to 2D)`
- Three struck-through wrong phrases + AI split -> `Format: stop_saying (3D to 2D)`
- Shared-frame AI partner or multi-scene grammar fixes -> `Format: live_conversation (3D to 2D)`
- Two-panel conversation from start to finish -> `Format: split_screen (3D to 2D)`
- Flashcard translation prompts + pitch -> `Format: translate_drill (3D to 2D)`
- Continuous single-frame narrative + flashbacks -> `Format: story_pitch (3D to 2D)`

## 2D Animation Editing Details
- **Visual verification**: Confirm active speakers and mouth closure on clip contact sheets, since stylized 2D animation has flatter lip contrast; choose clean closed-mouth spans for silent split `peer=` listener loops.
- **Camera & zoom discipline**: Keep all splits (`split_h`, `split_v`, `top_pip`, and baked splits) strictly at `z=1` with **14% headroom**. On full-frame `single` cuts, apply standard emotion zooms (`1.0->1.06`, `sudden_in:1.08@t`, `1.08` pause punch-ins) only when the 2D background is static — never zoom over drawn pans.
- **Captions, marks & transitions**: Keep `#subs auto y=65` (strict single-line only; `y=50` on `split_v`), place `mark:` / `banner:` / `-4 to 0 dB` beat & transition SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_*`, `kn20228_*`) with live non-frozen video visuals during post-word marks/SFX without dialogue overlap strictly per script cues, and apply only the chosen sub-format's story-driven transitions (including script-cued `warm_flash` / `dip_to_white` / `flash_zoom` at most once).

## Example Declaration & Cut
```text
# script_analysis.md line 1 -> Format: skit (3D to 2D)
L6 "2D_Nova_Split.mp4" 0.02-3.90 layout=split_h z=1 sp="Miss Nova" slot=left peer="Meera:2D_Meera_Idle.mp4"@1.20-5.08@right tr=split_reveal@0.40
```
