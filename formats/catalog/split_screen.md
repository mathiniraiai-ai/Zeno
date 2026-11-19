# Format 3: Split Screen (`split_screen`)

Declare on line 1 of `script_analysis.md`: `Format: split_screen (<sub-format>)`

## Sub-Formats & Orientation Rules
- **3.1 Horizontal (`split_v`)**: Stacked top-and-bottom panels (`1080x960` each). Writers often say "horizontal split" to mean a horizontal divider (`Top:` / `Bottom:`); always trust explicit `Top:`/`Bottom:` vs `Left:`/`Right:` labels first.
- **3.2 Vertical (`split_h`)**: Side-by-side left-and-right full-height panels (`536px` left, `4px` `#E1DBE1` divider, `540px` right).
- **3.3 Video call (`video_call`)**: Active speaker fills the frame while the partner sits in a rounded top picture-in-picture card (`top_pip` also supported for a fixed `750x420` top-center window).
- **3.4 With flash card**: Routed to `translate_drill`; uses `card:"English | Native"` (auto-generated bilingual top flashcard with face-safe placement, continuous across Robot+Woman pairs, cleared on full-screen takeover via `tr=whip_left`; never `banner:`).

## Four-Clip Talking + Silent Listener Workflow
- Two characters stay paired across the entire video using four clips: one speaking clip and one dedicated silent listener clip per character.
- Put the speaking character in their assigned `slot=left|right` (`split_h`) or `slot=top|bottom` (`split_v`) and attach the silent partner via `peer="Name:silent_clip.mp4"@start-end@slot`.
- Advance the silent listener window sequentially across turns so reactions look natural; if no dedicated listener clip exists, use a live quiet closed-mouth stretch (`<1.0s`, non-frozen) and note the fallback in `decisions.txt`.
- If source footage already contains a baked split seam, keep `layout=single slot=... z=1` and never composite it twice.

## Framing, Captions & Pacing
- **`z=1` always** on every split segment — zero pushes, pulls, or punch-ins. Maintain one fixed frame per character, matched backgrounds, and **14% headroom**.
- Keep dialogue joins tight (`0.06–0.15s`); inside a still split, cut internal pauses only when `>=0.50s` so natural breaths stay intact.
- **Captions**: Strict single-line subtitles only (`54px`); on `split_v`, always lock onto the center divider at `y=50`; on `split_h` and `video_call`, use `#subs auto y=65` clear of faces and banners.
- Drive emphasis through crisp cuts, spoken performance, scripted post-word `mark:wrong` / `mark:right` (`0.48s` over live non-frozen video visuals without dialogue overlap, paired with `sn_wrong.wav`/`buzzer.wav`/`w42_wrong.wav` `+1dB` or `correct_ding.wav`/`sn_correct.wav`/`w42_right.wav` `+0.5dB`), and varied `-4 to 0 dB` beat SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `kn20228_*`) — never place SFX on a silent listener.

## Transitions
- **Zero transitions inside the split.** Maximum **2 transitions** total: at most one entering and one leaving (`split_reveal@0.40`, `whip_left@0.34`, `whip_right@0.34`, `slide_up@0.36`, `slide_down@0.36`, or script-cued `warm_flash` paired with `split_reveal`).

## Example EDL Cut
```text
L3 "Nova_Talk.mp4" 0.03-3.65 layout=split_v z=1 sp="Miss Nova" slot=top peer="Learner:Learner_Idle.mp4"@2.10-5.72@bottom
```
