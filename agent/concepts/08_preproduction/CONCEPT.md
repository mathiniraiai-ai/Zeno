# 08 · Pre-production (Veo prompts, before any clip exists)

Use this when the user has a **script but no clips yet** and will generate the reel in Google Flow / Veo 3.1. Good prompts mean
fewer retakes, the right character speaking each line, and clips Zeno can map to the script automatically.

## The split
The Engine does the exact parts. You write the creative parts.

| Engine (`scripts/preproduce.py`) | You (the Director) |
|---|---|
| Numbers the shots D-1, D-2, ... one per script line (a silent beat is a shot too) | `characters.json`: look, outfit and voice of each speaker, once |
| Copies each line word for word into `audio.dialogue` | Per shot: `style`, `composition`, `camera_motion`, `lighting_mood`, `expression`, `action`, voice `tone`, `negative_prompt` |
| Fixed JSON schema, fixed key order | The first-frame prompt in `frames/D-n.txt` |
| Copies each character into all their shots (`apply`), so looks and voices match | |
| Flags lines too long for one 8 s clip (about 19 words); proposes sentence-end splits | Decide how to split a long line (ask the user, never split silently) |
| Names the clips `<project>_D-n.mp4`; writes `shots.md` for copy-paste | |

## Commands
```bash
python3 scripts/preproduce.py init  input/script/<name>.txt --project <reel-code> --lang ml [--reference <image>]
# fill characters.json, then every shots/D-n.json and frames/D-n.txt
python3 scripts/preproduce.py apply <Downloads/Zeno Outputs/<reel-code>_preproduction>
python3 scripts/preproduce.py check <same folder>      # must say OK; fix every ERROR
```
Everything is written to `~/Downloads/Zeno Outputs/<reel-code>_preproduction/`. Give the user `shots.md` (frame prompt + JSON per shot).

## Writing rules for the shots
- **Dialogue**: exactly the script line, in its own script (Malayalam, Kannada, code-mixed English as written). One uninterrupted
  delivery: no pauses, ellipses, split lines or fragmented delivery notes. (Pauses Veo adds anyway are cut later by Zeno.)
- **One speaking subject per shot.** The speaker comes from the script. Never ask one clip to carry two characters talking.
- **Reference first**: carry the reference image's style, character appearance, wardrobe, setting, lighting and framing unless the script changes them.
- **Simple and explicit**: clear placement, action, lighting and camera direction. No subtle effects, no ambiguous wording.
- **film_grain** `"none"` unless the user asks. **camera_motion** restrained: `"static opening frame"` is usually right; a slow push-in at most for emotional beats.
- **Voice**: same accent, nationality, gender and pitch for a character in every shot (`apply` does this); `speed` 1.0 unless asked; accent matches the language.
- **negative_prompt**: only failure modes that matter here, for example extra people speaking, lip-sync drift, subtitles or on-screen text,
  distorted hands or faces, background music, wardrobe changes.
- **Silent beats** (`(Man takes out his phone)`): a shot with no dialogue; put the action in `subject.action`; start the line with the character's name so `apply` gives it the right look.
- **First-frame prompt**: the one image that shows the story moment before motion starts: subject placement, wardrobe, setting, light, framing, emotion. If an image tool is connected, ask once whether to generate the frames with it.

## Hand-off
Tell the user: generate each shot in Flow (frame first, then the JSON as the video prompt) and save the clips as
`<reel-code>_D-<n>.mp4`. Retakes can sit beside the main clip: Zeno transcribes all of them and keeps the best.
Then run the normal edit (`concepts/02_footage_intake`).
