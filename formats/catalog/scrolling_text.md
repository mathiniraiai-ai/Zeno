# Format 4: Scrolling Text (`scrolling_text`)

Declare on line 1 of `script_analysis.md`: `Format: scrolling_text (4.2 Two people)`

## Sub-Formats & Availability
- **4.1 One person**: **Not built.** Only two-person `#page scroll` is implemented in the renderer. If a script requests single-person scrolling text, stop and clearly inform the user that only two-person `#page scroll` is built.
- **4.2 Two people in side-by-side split (`#page scroll`)**: Used whenever the script asks for "scrolling text", "scrolling subtitles", or "scrolling transcript" with two speakers.

## Canvas & Page Geometry (`1080x1920`)
- **Top half (`932px` high)**: Two speakers placed side by side in `layout=split_h z=1` with no vertical divider between panels, underlined by a `20px` blue rule (`#1F4DA1`).
- **Bottom half (`968px` high)**: Clean white scrolling page (`text_size=44`, left margin `216px`) rendered one paragraph per speaker turn, with a live highlight box tracking each spoken word from transcript timestamps.

## Plan Header & Cut Rules
- Add `#page scroll` to the `plan.edl` header. Keep optional flags (`bar`, `dash`, `fade`, and wrong/right phrase colors) off unless explicitly requested by the user.
- Every cut must use `layout=split_h z=1` with explicit `slot=left|right` and a silent `peer="Name:silent_clip.mp4"@start-end@slot` advancing smoothly across turns.
- Keep `0.06–0.15s` turn joins and cut internal pauses only when `>=0.50s`.

## Motion, Omissions & Transitions
- **Still picture throughout**: Lock every segment at `z=1`. The scrolling page supplies all visual motion; the video panels never zoom or pan.
- **Strict omissions**: Omit `#subs`, `banner:`, extra overlays, `mark:`, and `sfx:` completely — the page itself carries all text and emphasis.
- **Zero transitions (`max_transitions = 0`)**: Use hard cuts (`tr=cut`) on every segment.
- **Post-render check**: Inspect the rendered scrolling page to verify transcript spelling and word-box timing accuracy, reporting any transcript defects.

## Example EDL Plan Snippet
```text
#page scroll
L1 "Teacher_Talk.mp4" 0.02-3.40 layout=split_h z=1 sp="Teacher" slot=left peer="Student:Student_Silent.mp4"@0.00-3.38@right
```
