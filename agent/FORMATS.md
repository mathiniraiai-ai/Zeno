# Formats: what each kind of video is, and how to edit it

The team's format list is the "Testing Formats" tab of the editing sheet: **8 main formats, most with sub-formats**. Name the sub-format **before** the emotion plan and write it on the first line of `work/<project>/script_analysis.md` as `Format: <id> (<name>)`, for example `Format: translate_drill (How would you translate)`. `--check-plan` reads that line and checks the plan against the format's edit pattern (`agent/formats.json` holds the same numbers for the code). If the script fits no format, write `Format: none` and say why.

Sources: **[sheet]** the Testing Formats tab (names, priority, example links), **[scripts]** read from example scripts, **[videos]** measured on delivered videos and the editors' comments, **[none]** no example yet. Correct anything that is wrong.

## Transitions follow the story, never a clock (every format)
Editors' feedback (Bruno and Jerin, W45, 2026-11-13): the wipes, dissolves and white or warm flashes were "too much". On two delivered videos, 13 of 20 transitions sat between two clips of **the same shot** (same set, same framing), where they show the jump instead of hiding it. They were there because the old rules asked for "a transition every 7 to 9 s" and for 3 kinds per reel. Those rules are gone.
- **A transition marks a change the viewer should feel:** a new place or time (a scene heading, "Scene changes", "Back to"), entering or leaving a split, a hook into a lesson, or the lesson into a separate call-to-action shot. Everything else is a hard cut. Zero transitions is a good result.
- **Never** between two cuts of the same shot, inside one script line (`L5a` to `L5b`), inside a split, or inside a continuous conversation. A jump inside one shot is hidden with a `1.08` punch-in, not a transition.
- **Pick the kind by purpose:** a new place: `whip_left`/`whip_right`/`slide_up`; entering or leaving a split: `split_reveal`; a time jump: `dip_to_black`; into or out of a flashback: `dissolve`. `warm_flash`, `dip_to_white` and a `flash_zoom` transition only when the script asks for a flash, and at most once.
- Each kinetic transition brings its own whoosh, so fewer transitions also means fewer sound effects.

**What most micro-narratives share [scripts]:** a human hook or problem, a bridge ("I used to speak like this too", "let me show you"), a drill with the AI teacher (Miss Nova or the Female Robot) in which a wrong sentence is corrected and praised, then the install call to action. The peaks are the humiliation or surprise early and the correction or the "Wow" at the payoff.

## 1. Micro narrative (URGENT; every sub-format has a vertical and a horizontal version)

### `skit` — 1.1 Skit based micro narrative
- **Script [scripts]:** a `Visual:` paragraph and a reference link, a character list, bold speaker labels, tone notes in parentheses (`(making fun)`), cut notes in brackets; 30 to 40 spoken lines. Human actors in several places (an airplane seat, a crew cabin, a construction site); the Female Robot only in the split. A humiliating incident or hook, a stranger or flashback coaches one line, then the robot drills it. Marks only where the script puts them.
- **Layouts:** `close_up`/`single` for the live scenes, then `split_h`/`split_v` (or `top_pip`) for the drill; a two-shot or hologram stays `single`.
- **Edit pattern:** snappy speaker-follow cuts with emotion zooms in the live part; still split for the drill.
- **Transitions:** one at each scene heading that changes place or time, one into the split, at most one out. Hard cuts inside a scene.

### `direct_hook` — 1.2 Direct hook micro narrative
- **Script [scripts]:** `Concept reference`, `Visual:`, `Characters:`, `Scene 1/2/3`; 20 to 25 lines; often repeated as variants. A selfie talking head (live or 3D) hooks straight to camera, then a split with the AI teacher (teacher left, human right), then the call to action.
- **Layouts:** `selfie` (`single`), then `split_h`.
- **Edit pattern:** the hook moves (punch-in on the first strong word); the split is still.
- **Transitions:** one into the split (`split_reveal`), at most one out to the call to action. 2 at most.

### `street_interview` — 1.3 Street interview micro narrative
- **Script [scripts]:** a reference link, `Characters`, `Scene 1/2`; 20 to 30 lines. A handheld street-and-microphone look with an unusual interviewee (a couple abroad, an old woman asking for a job, a bus driver) who answers fluently; the reveal is the AI teacher, a "let me show you" bridge, a split with Miss Nova, the call to action.
- **Layouts:** `selfie`/`close_up` (`single`, handheld: zoom only where the camera is steady), then `split_h`/`split_v`.
- **Edit pattern:** hard cuts between interview beats; still split.
- **Transitions:** one into the split at the bridge, at most one out. 2 at most.

### `stop_saying` — 1.4 Stop saying / Never say micro narrative
- **Script [scripts]:** a reference link, `Characters`, `Scene 1` to `5`; 16 to 24 lines; 79 to 101 s. Opens "Stop saying / Never say X"; three stock phrases struck through with a red X and a wrong sound as each is said; a bridge; a split drill with the robot and a green tick on the corrected sentence; a return to scene 1; the call to action. A live or 3D presenter (a scooter selfie, the sea, a talk show).
- **Layouts:** `single` with a `banner:` stack for the struck phrases, then `split_h` or `split_v`, then `single`.
- **Edit pattern:** the struck phrases are the peak: banners and marks carry them, not motion.
- **Transitions:** none between the struck phrases; one into the split, one back to scene 1. 3 at most.

## 2. Live conversation (URGENT) — the robot is in the same shot as the human: one full frame, never a split

### `live_conversation` — 2.1 Hologram, 2.2 AI teacher on a tablet with the human doing an activity, 2.3 Interactive (driving a cycle, on a bull, a road accident); also live grammar correction
- **Script [scripts][videos]:** a `Visual:` line and few or no scene numbers; 12 to 20 lines. The robot is a hologram, a device in the same shot (a giant phone beside the bed, a tablet), or stands beside the human. A mistake or a rude request the robot corrects, the human repeats it, objections about judgment, time and cost are answered, the call to action, sometimes a cut to the app screen. A **live grammar correction** (W45 Malayalam "speak more politely", 123 s) runs longer: one short scene per correction, each in a new place, then one continuous conversation, then the robot full screen. Hologram and interactive: no example script yet **[none]**.
- **Layouts:** `single` (two-shot: zoom only with a safe `focus=`; hologram: `z=1`); `video_call` when the robot is on a screen.
- **Edit pattern:** speaker-follow cuts and emotion zooms; pauses hidden with `1.08` punch-ins.
- **Transitions:** one per real change of place (each correction scene in a new location), one into a separate call-to-action shot. **None inside the continuous conversation** ("They continue talking").

## 3. Split screen (HIGH; with flash card MEDIUM)

### `split_screen` — 3.1 Horizontal, 3.2 Vertical, 3.3 Video call, 3.4 With flash card
- **Script [sheet]:** two characters for the whole video, `Left:`/`Right:` or `Top:`/`Bottom:`, or a call. No example script yet.
- **Layouts:** `split_h`, `split_v`, `video_call`; with flash card: `chat_bar` or `banner:` cards over the split (not built as its own layout yet).
- **Edit pattern:** four clips (a talking and a silent clip per character), the listener rule, `z=1` always; emphasis from the cut, the words, the marks and the sound.
- **Transitions:** none inside the split; at most one in and one out.

## 4. Scrolling text (HIGH)

### `scrolling_text` — 4.1 One person, 4.2 Two people in a vertical split
- **Script [sheet]:** the script says "scrolling text". Two people is built: `#page scroll` (see `EDIT_RULES.md`, "Scrolling-text page format"; recipe `styles/formats/scroll_page.json`). **One person is not built**: tell the user.
- **Layouts:** `split_h` segments under `#page scroll`.
- **Edit pattern:** one fixed frame; the page moves, the picture never does. No zoom, no banners, no `#subs`.
- **Transitions:** none.

## 5. How would you translate (MEDIUM)

### `translate_drill` — flash card drill
- **Script [scripts][videos]:** one AI teacher (usually the Female Robot) in one set; a run of `Flashcard Text:` lines, each followed by the teacher: "How do you say <mother-tongue phrase> in English? <English>. Say it with me: <English>". Then one long pitch block and the call to action. Example: W45 Malayalam "Robot in office" (7 cards, 107 s).
- **Layouts:** `single` with the flashcard as a `banner:` (or `chat_bar` with `label=`), same framing card after card.
- **Edit pattern:** the card change is the beat: the new card appears on the teacher's first word, optionally with `click.wav` or `pop.wav` at `-3`. Alternate `z=1.0` and a slow `1.0->1.06` push between cards so consecutive cards do not look frozen. The pitch: slow pushes, pauses hidden with punch-ins.
- **Transitions:** **none between cards** (same shot). A hard cut into the pitch; one `whip` only if the pitch is a different shot. 1 at most.

## 6. Skit based / story based (MEDIUM)

### `story_pitch` — direct pitch
- **Script [scripts]:** five scenes, a `Visual:` line on each, about 11 long narration blocks and some flashback lines; one continuous kitchen shot plus faded flashbacks; a hologram of Miss Nova above the phone; the call to action straight to camera.
- **Layouts:** `single` throughout, no split, no marks.
- **Edit pattern:** long narration: slow pushes on teaching blocks, pauses hidden with punch-ins.
- **Transitions:** `dissolve` into and out of each flashback; everything else is a hard cut.

## 7. Realistic video to 3D (MEDIUM)

### `realistic_to_3d` — **[none]**
- A live-action story re-made as 3D animation. No example script or video yet. Edit it by the sub-format its script follows (usually 1.1 to 1.4) and say so in the format line, for example `Format: realistic_to_3d (as skit)`. Same transition rules.

## 8. 3D to 2D (MEDIUM)

### `3d_to_2d` — a generation format only
- How the footage was generated (a 3D story re-made as 2D animation), not a way of editing. Name the sub-format the script follows in the format line (for example `Format: skit (3D to 2D)`) and edit by that; nothing else changes.

## The layout recipes in code (`styles/formats/*.json`)
`format_library.py` matches the script's wording to these recipes and sets `layout_hint` in `line_map.md`; a format above uses one or more of them.

| Recipe | Layout | Used by |
|---|---|---|
| `close_up`, `close_up_w43` (measured) | `single` | skit, street_interview, live_conversation |
| `selfie` | `single` | direct_hook, street_interview, stop_saying |
| `split_h` (measured) | `split_h` | skit, direct_hook, street_interview, stop_saying, split_screen |
| `split_v` (measured) | `split_v` | skit, street_interview, stop_saying, split_screen |
| `video_call` | `video_call` | split_screen (call), live_conversation (robot on a screen) |
| `chat_bar` | `chat_bar` | translate_drill, split_screen with flash card |
| `scroll_page` (measured) | `split_h` + `#page scroll` | scrolling_text (two people) |

## Things the examples show
- **Writers use "vertical" and "horizontal" split loosely.** Trust the explicit `Left:`, `Right:`, `Top:`, `Bottom:` in the script.
- **A script often holds competitor transcripts and several variant tabs.** Edit the Supernova script of the tab you were given.
- **Marks differ by script, not by format.** Put a red X or a green tick only where that script marks a line.
- **Footage is mostly AI-generated** (3D or live-action looks); a character may be silent in one clip and speaking in another, so check the clip sheet for who is who.
- The cards show lengths of 79 to 123 s, in Telugu, Hindi, Malayalam and Kannada.
