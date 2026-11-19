# Format 7: Realistic Video to 3D (`realistic_to_3d`)

Declare on line 1 of `script_analysis.md`: `Format: realistic_to_3d (as <subformat>)`

## Concept & Sub-Format Identification
- A live-action story remade as 3D character animation (for example, `Format: realistic_to_3d (as skit)`, `Format: realistic_to_3d (as direct_hook)`, or `Format: realistic_to_3d (as live_conversation)`).
- Because 3D rendering defines asset look rather than dramatic structure, identify the underlying narrative pattern the script follows (usually Micro-Narrative sub-formats `skit`, `direct_hook`, `street_interview`, `stop_saying`, or `live_conversation`) and edit strictly by that pattern.

## Layouts & 3D Footage Handling
- Follow the underlying sub-format's layout sequence: `layout=single` (`close_up` or `selfie` with measured `focus=x,y`) for 3D hook/story scenes, moving to `split_h` or `split_v` for two-panel AI drills (or staying `single` for shared 3D two-shots and holograms).
- **Baked vs separate 3D splits**: If a 3D render already has a baked split seam, use `layout=single slot=left|right|top|bottom z=1` and never double-composite. For separate 3D clips, pair the active speaker with a dedicated silent 3D listener clip via `peer=` at `z=1` and **14% headroom**.
- Enforce strict single-line subtitles only: lock `split_v` subtitles to the center divider at `y=50`; use `#subs auto y=65` on full-frame and `split_h` cuts.

## Motion, Cuts & Effects
- On steady full-frame 3D shots, cut speaker-follow turns at `0.06–0.15s` joins, apply `1.0->1.06` pushes on longer turns (`>=2.5s`), use `sudden_in:1.08@t` on key emotional beats, and hide `>0.25s` monologue pause cuts with `1.08` step punch-ins.
- Never zoom split panels (`z=1`), 3D holograms (`z=1`), or 3D renders that already have baked virtual camera pans or dollies.
- Add post-word `mark:wrong` / `mark:right` (live non-frozen video visuals without dialogue overlap), `banner:` strike phrases, and varied `-4 to 0 dB` beat/transition SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_*`, `kn20228_*`) only where scripted or on earned beats.

## Story-Based Transitions
- Follow the exact transition rules and caps of the underlying sub-format: place transitions (`whip_left@0.34`, `whip_right@0.34`, `slide_up@0.36`, `slide_down@0.36`, `split_reveal@0.40`, `dip_to_black@0.40`, `dissolve@0.40`, `fade`, `zoom_burst`, plus script-cued `warm_flash`, `warm_flash_punch`, `dip_to_white`, `flash_zoom` at most once) only at real scene/time changes, split entry/exit, or a separate CTA shot. Hard-cut all same-shot and intra-scene edits.

## Example EDL Header Note
```text
# script_analysis.md line 1 -> Format: realistic_to_3d (as skit)
L1 "3D_Scene1_Man.mp4" 0.03-3.15 layout=single z=sudden_in:1.08@0.65 sp="Raju" focus=0.50,0.34
```
