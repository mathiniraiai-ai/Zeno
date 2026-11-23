# Format 6: Story-Based Direct Pitch (`story_pitch`)

Declare on line 1 of `script_analysis.md`: `Format: story_pitch (Direct pitch)`

## Script Structure & Cues
- Multi-scene narrative monologue (typically ~5 scenes with `Visual:` descriptions and ~10–12 extended narration blocks) anchored in one continuous primary setting (such as a kitchen or living room) interspersed with brief illustrative flashbacks.
- May include an in-frame AI teacher hologram moment (such as Miss Nova above a phone) before concluding with a direct-to-camera install call to action.

## Layouts & Framing
- **`layout=single` throughout** the entire video — never switch to `split_h`, `split_v`, `video_call`, or `chat_bar`.
- Apply measured `focus=x,y` on every cut to keep speaker framing and headroom steady across primary narration and flashback inserts.
- Keep default subtitles `#subs auto y=65` (strict single-line subtitles only; `y=50` on `split_v`).

## Pacing, Zooms & Cuts
- **Teaching & story blocks**: Use slow, composed `z=1.0->1.06` pushes across longer spoken blocks (`>=2.5s`), alternating with calm `z=1.0` beats so movement retains weight.
- **Sentence integrity & punch-ins**: Keep every spoken sentence complete (`start - 0.05s` through `end + 0.06–0.10s`, verified against `pauses:` in `editing_packet.md`; `0.06–0.15s` turn joins) and never cut `<0.45s` mid-sentence breaths or commas. Trim only genuine dead-air pauses `>=0.50s`; for `1.08` emphasis, either punch in at a real `>=0.45s` pause in `pauses:` or use `z=sudden_in:1.08@t` (`0.05s` before the stressed word) inside the continuous cut without slicing the audio.
- **Hologram & moving-camera rule**: Keep any shot featuring an in-frame 3D hologram or baked camera pan/dolly locked at `z=1.0`.

## Marks, Graphics & Sound
- Omit `mark:wrong`, `mark:right`, and strike banners unless explicitly called for by the script (when used, keep live non-frozen video visuals during post-word marks/SFX without dialogue overlap).
- Keep sound effects sparse and subtle (`-4` to `-3 dB` on major reveals/flashbacks only, choosing from `pop.wav`, `click.wav`, `sn_punch_1.wav`, `sn_riser_1.wav`, `whoosh.wav`, `sn_swish_1.wav`, `w42_transition.wav`) so the storytelling voice carries the edit.

## Transitions
- Place `tr=dissolve@0.40` (or `fade` / `dip_to_black@0.40` on explicit time jumps; script-cued `warm_flash` / `dip_to_white` at most once when requested) **into and out of each flashback** to separate memory beats from present-time narration.
- Use **hard cuts (`tr=cut`) everywhere else** — never add transitions between consecutive narration cuts in the primary location.

## Example EDL Cut
```text
L5 "Flashback_Office.mp4" 0.05-4.80 layout=single z=1.0->1.06 sp="Narrator" focus=0.50,0.33 tr=dissolve@0.40
```
