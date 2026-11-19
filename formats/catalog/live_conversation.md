# Format 2: Live Conversation (`live_conversation`)

Declare on line 1 of `script_analysis.md`: `Format: live_conversation (<sub-format>)`

## Sub-Formats & Script Cues
- **2.1 Hologram in-frame**: The AI teacher appears as a 3D hologram inside the same room and camera frame as the human speaker.
- **2.2 AI teacher on tablet with activity**: The AI teacher appears on a tablet or large phone screen in-shot while the human performs an activity, or via `video_call` when shown as a device call.
- **2.3 Interactive scene**: Human and AI teacher share live physical action in-frame (cycling, riding, roadside incident, or standing side by side).
- **2.4 Live grammar correction across scenes**: Short sequential correction scenes across distinct locations, followed by one continuous shared conversation and an optional full-screen AI teacher CTA.
- **Typical arc**: `Visual:` setup with 12–20 spoken lines (longer in multi-scene grammar corrections). The human makes a mistake or blunt request, the AI teacher corrects it, the human repeats it fluently, objections (judgment, time, cost) are answered, ending with the install CTA.

## Layouts & Framing
- **Allowed layouts**: Always `single` or `video_call` — **never** `split_h` or `split_v`, because both characters already share the frame or call screen.
- **Hologram framing**: Lock every 3D hologram clip at `z=1` so floating projections stay anchored.
- **Live two-shots**: Zoom only within measured safe framing (`<=1.10`) using explicit `focus=x,y` so neither face is clipped at frame edges.

## Cuts, Motion & Sound
- Use conversational speaker-follow cuts (`0.06–0.15s` dialogue joins).
- On solo full-frame turns (`single` without hologram or camera pan/dolly), use `1.0->1.06` slow pushes on sustained lines (`>=2.5s`) and `sudden_in:1.08@t` (`0.05s` before the stressed word) on emotional peaks.
- Cut internal monologue pauses `>0.25s` and mask same-shot joins with a `1.08` step punch-in.
- Apply `mark:wrong@t/0.48@style=flash` (`sn_wrong.wav`/`buzzer.wav`/`w42_wrong.wav` `+1.0 dB`) and `mark:right@t/0.48@style=flash` (`correct_ding.wav`/`sn_correct.wav`/`w42_right.wav` `+0.5 dB`) with a `0.35–0.42s` tail only where scripted. Keep `#subs auto y=68`.
- Punctuate selected teaching/reaction beats with varied `-4 to 0 dB` SFX (`pop.wav`, `click.wav`, `sn_click.mp3`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_*`, max 1/beat, `<=40%`/file).

## Transitions
- Place **one** transition (`whip_left@0.34`, `whip_right@0.34`, `slide_up@0.36`, `slide_down@0.36`, `dip_to_black@0.40`, `fade`, or `zoom_burst`; plus script-cued `warm_flash`, `warm_flash_punch`, `dip_to_white`, `flash_zoom` at most once when requested) per genuine change of location (such as each new grammar-correction scene) and at most one into a separate CTA shot.
- **Zero transitions** inside continuous conversation ("They continue talking"), inside one script line, or between cuts of the same shot.

## Example EDL Cut
```text
L4 "Kitchen_TwoShot.mp4" 0.04-4.12 layout=single z=1 sp="Miss Nova" focus=0.52,0.34
```
