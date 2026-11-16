# Reference: layouts, mark packs, plan syntax, SFX

Moved out of CLAUDE.md so it is read only by an edit that needs it (`brief.md` includes it for every format except the scrolling page). Rules about decisions live in `agent/EDIT_RULES.md`.

## Layout modes, split screens and mark packs

| Layout Mode | Visual Layout (`1080×1920`) | When to Use |
|---|---|---|
| `single` | Full-frame clip (`zoom=1.08` jump-cut punch-in, `zoom=1.0->1.06` slow push, `flash_zoom`). | Solo teaching lines, hooks, live conversation, **OR pre-stitched Google Flow vertical split-screen clips** (match as `single` with `slot=top\|bottom\|left\|right` or `active=top\|bottom\|left\|right`, always `z=1`: split panels never zoom). |
| `split_v` | Top/bottom 50/50 split (`1080×960` each) with stationary divider (`#subs auto` at `y=50` seam). Both panels stay still at `1.0` in one fixed frame (no zoom, ever); beat SFX and marks carry the emphasis. | Two-character Q&A when Character A and Character B are **separate raw files** (e.g., HeyGen renders). |
| `split_h` | Left/right 50/50 vertical split (`540×1920` each) with stationary divider. Both panels stay still at `1.0` in one fixed frame (no zoom, ever); beat SFX and marks carry the emphasis. | Side-by-side split screen when Character A and Character B are **separate raw files**. |
| `chat_bar` | Full-frame speaker + frosted glassmorphic prompt bar (`label="Phrase..."`). | Language drills ("How to say X?"). |
| `video_call` | Full-frame speaker + rounded Picture-in-Picture (PiP) card (`pip=S1@3.2`). | Remote call / FaceTime style dialogues. |
| `split_h` + `#page scroll` | **Scrolling-text page** (as the in-house "return back" reference): the two characters' vertical clips stitched side by side in the top half (`540×932` each, still, no zoom, no line between them), a blue line under them, and a plain white page below on which the whole dialogue scrolls up as it is spoken, with a box on the word being spoken. Nothing else is added. | The script says **scrolling text**. `--inspect` writes `#page scroll` by itself; every segment is `layout=split_h z=1`; `#subs` is not used. |
| `hybrid` | Mixes `single` on teaching monologues with `split_v`/`video_call`/`chat_bar` on dialogue turns. | **Default (`auto`)** — best retention. |

- **Split screens never zoom (owner's rule, overrides any older wording in this file).** Every `split_h`, `split_v` and baked-split segment is `z=1`: no push-in, punch-in, step zoom or pull-out. The renderer ignores a zoom written on a split and holds each panel in one fixed frame (14% headroom, the speaking and the silent clip of a character lined up by their background), so switching between them never jumps.
- **Captions (owner's rule, overrides any older wording in this file).** `#subs auto y=68` is the default. One style is used for the whole video, at one fixed size (54 px on a 1080-wide picture; `size=` changes it), always on a single row (never two rows), with no per-caption colour changes unless the plan says `adapt=on`. On full-frame cuts captions sit at 65 to 70% of the height (`y=68`). In a top/bottom split they always sit on the divider line in the middle (`y=50`), whatever `y=` says, so neither face is covered.
- **Job archive and feedback (owner's rule).** Every edit is stored end to end on the Zeno server by itself (script, every version of the plan, what each check said, verification, token numbers, the editor's prompts and the agent's steps and errors, the stage log, the Zeno version), including edits that stop before the render: nothing to do for that. What only you can add is the human's words: whenever the user comments on the plan or the finished video, or asks for a change ("the head is cut in L4", "subtitle too big", "this one is perfect"), first run `<PY> scripts/zeno.py --feedback "<their words, as said>" --plan work/<project>/plan.edl`, then act on it. One call per comment; it takes a second and never blocks the job.
- **Listener rule (every split: left/right or top/bottom).** While one character speaks, the OTHER panel shows the other character, silent and listening: left speaks -> right listens, right speaks -> left listens, and the same for top and bottom. Who sits where comes from the script (`Top: Robot | Bottom: Lady 1`, or `Woman - Right screen | Miss Nova - Left screen`, or "left screen" in the character list). The picture comes from that character's silent clip: put the character's name in the file name (`Robot_listening.mp4`, `Lady1_idle.mp4`); an unnamed silent clip is matched by how it looks (colours) and is left unassigned if unsure. Silent clips are walked through turn after turn, never restarted from the same frame. With no silent clip, a quiet mouth-closed moment of the character's own clip is used (a still frame if it is under 1 s) and the brief says `[LISTENER_FALLBACK]`; `[LISTENER_GUESS]` and `[LISTENER_MISSING]` mean a silent clip could not be tied to the character or does not exist. A character that lands in two panels is reported as `[PANEL_FLIP]`.

**Swappable `✓`/`✗` Mark Packs (`--mark-pack` or `# mark_pack:`)**:
- `flash` (**default**): Full-frame `1080×1920` subtle `0.50` alpha green/red border overlay (`assets/marks/green_flash.png`, `red_flash.png`) that triggers **at the end of the wrong/right sentence** (`0.35–0.42s` post-word hold tail) and **flashes 2 times (`0.48s`: Pulse 1 → OFF → Pulse 2)** with calibrated post-`loudnorm` `+1.0 dB` error SFX (`sn_wrong.wav`) and `+0.5 dB` right ding (`correct_ding.wav` + `sn_correct.wav`).
- `vignette`: Full-frame radial glow (`assets/marks/w42_green_vignette.png`, `w42_red_vignette.png`, `0.50` alpha 2-pulse at sentence end).
- `default`: Flat circle badge (`assets/marks/right.png`, `wrong.png`)
- `glossy`: 3D glass gradient badge (`assets/marks/glossy_right.png`, `glossy_wrong.png`)
- Custom: Place `right.png` & `wrong.png` in `assets/marks/<pack>/`.
- **Transitions follow the story, never a clock (owner's rule, W45 editor feedback; overrides any older wording).** Each format in [`agent/FORMATS.md`](agent/FORMATS.md) (the 8 formats of the team's Testing Formats sheet) has its own edit pattern. A transition marks a new place or time, entering or leaving a split, or a separate call-to-action shot; everything else is a hard cut, and zero transitions is fine. Never between two cuts of the same shot, inside one line, or inside a split. Kind by purpose: `whip_left`/`whip_right`/`slide_up` for a new place, `split_reveal@0.40` into or out of a split, `dip_to_black` for a time jump, `dissolve` for a flashback; `warm_flash`/`dip_to_white`/`flash_zoom` only when the script asks, once at most. `--check-plan` prints `[STYLE] TRANSITION` lines for any that break this.
- **Skill Links**: [`.agent/skills/video-edit/SKILL.md`](.agent/skills/video-edit/SKILL.md) | [`styles/micronarrative/SKILL.md`](styles/micronarrative/SKILL.md) | [`agent/DIRECTOR.md`](agent/DIRECTOR.md) | [`agent/concepts/word_boundary_training.md`](agent/concepts/word_boundary_training.md)

## `plan.edl` quick syntax (pipe form) and the SFX library

```text
# output: output/Kannada_I_am_hungry.mp4
# canvas: 1080x1920@30
# preset: hybrid
# mark_pack: flash
# loudness: -11.5
#subs auto y=68

# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note
L1 | S1 | 1.12-5.40 | single | sudden_in:1.08 | cut | focus=0.5,0.45 | Hook question
L2 | S2 | 0.85-4.62 | split_v:top=S1@5.48 | 1.0 | split_reveal:0.40 | active=bottom | Wrong student attempt (split panels stay still)
L3 | S3 | 1.05-5.88 | chat_bar | 1.08 | whip_right:0.34 | label="ನನಗೆ ಹಸಿವಾಗಿದೆ" | Teacher correction

# MARKS: type | segment | at | duration | pos | size | sfx | gain_db
wrong | L2 | 3.35 | 0.48 | top-right | 0.22 | sn_wrong.wav | +1.0
right | L3 | 4.40 | 0.48 | top-right | 0.22 | correct_ding.wav | +0.5

# SFX: file | segment | at | gain_db
pop.wav | L1 | 0.20 | -3
whoosh.wav | L3 | 0.0 | -2
```
- **Calibrated Post-`loudnorm` SFX (`assets/sfx/`, `# SFX:` `-4 to 0 dB`, `# MARKS:` `+1.0` wrong / `+0.5` right)**: Right: `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav` | Wrong: `buzzer.wav`, `sn_wrong.wav`, `w42_wrong.wav` | Transitions & Beat Accents: `whoosh.wav` (`-3 to -2`), `sn_swish_1.wav` (`-3 to -2`), `pop.wav` (`-3`), `sn_punch_1.wav` (`-2 to -1`), `sn_riser_1.wav` (`-3 to -2`), `click.wav` (`-3`).
