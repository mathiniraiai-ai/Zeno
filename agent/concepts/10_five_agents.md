# Five agents: four build the edit, one checks the finished video

Zeno (Python) transcribes (Gemini), aligns (wav2vec2), measures and renders. It does that once, in `--inspect`. **No agent repeats it**: Agents 1 to 4 never call Gemini or the server, and Agent 5 never does either.

The Director (the session you are in) merges the work of Agents 1 to 4 into `plan.edl`. Where the host can start sub-agents (Claude Code `Agent` tool, Antigravity agents), run them as sub-agents; where it cannot, the Director does the four passes one after another with the same checklists. Either way each agent reads only its own files and hands back a short result (at most 1.5k tokens), so the 50k to 70k budget holds.

## Agents 1 to 4: the edit

| Agent | Reads | Decides | Hands back |
|---|---|---|---|
| **1. Script & Take Auditor** | `line_map.md`, `facts.md`, the script | Which clip and take says each script line, who speaks it (`sp=`), script order. Names every line the footage cannot deliver and every wording that differs from the script (for example "ten to fifteen" said where the script says "fifteen to twenty"). | The timeline: `id`, `src`, `sp` per segment, plus a list of gaps and differences for the user. |
| **2. Cut & Pause Editor** | `facts.md` | In and out of every segment: pauses over 0.30 s, repeats and false starts, no cut inside a word, silence across a join of 0.20 s or less. | `start-end` per segment. |
| **3. Layout & Motion Director** | the script's layout cues, `clip_sheet_<n>.jpg`, `script_analysis.md` | The layout the script asks for (a split stays a split for all of its dialogue), the peer windows of the silent clips, zooms by emotion, at most three peaks. Splits and framing: same eye line and face size in every panel and every cut. | `layout`, `z`, `focus`, `peer`, and `script_analysis.md`. |
| **4. Transitions, Marks & Sound** | `script_analysis.md`, `EDIT_RULES.md` | Transitions, ✗/✓ marks only where the script marks them, banners, effect sounds, volume. | `tr`, `mark`, `banner`, `sfx`, `vol`. |

The Director writes every segment line from these four results, runs `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes, shows the user the plan and asks Yes/No once.

## Agent 5: the final check (after `--execute`)

**Rule: zero Gemini calls, zero server calls, no `--inspect`, no `--fresh`, no re-transcribing. Local code and your own eyes and ears only. At most 10k tokens.**

Inputs: the rendered video, `plan.edl`, `line_map.md`, `facts.md`, the `verify` lines, `review_sheet.jpg`, `marks_contact_sheet.jpg`, the local FFmpeg (`tools/ffmpeg/ffmpeg`). Do not open `words.json`, `silences.json` or `align.json` unless one specific word timing is in doubt.

1. **Script.** Every script line once, in script order. When `verify` says N-1 of N, name the missing line: compare the ids in `line_map.md` with the ids in the plan and the words inside each cut (`facts.md`).
2. **Timestamps.** Each cut starts and ends outside a word and inside the gap `facts.md` shows; the silence across each join is 0.20 s or less; each ✗/✓ mark sits at the end of its sentence (`marks_contact_sheet.jpg`).
3. **Picture.** Tile a frame batch and look at it: one frame every 6 s, plus the first and last frame of every layout block.
   `tools/ffmpeg/ffmpeg -i <video> -vf "fps=1/6,scale=225:-1,tile=8x3" -frames:v 1 batch.jpg`
   Check: the layout the script asked for holds (a split is not left); in a split both heads sit at the same height and the same size, in every panel and from cut to cut; no face is cut off or covered; no black frame; text never on a face. If the user gave a reference video, tile it the same way and compare the two batches.
4. **Audio.** `silencedetect=n=-45dB:d=0.85` finds dead air; the loudness is -11.5 LUFS within 1 dB with a true peak of -1.0 dBTP or lower; effects sit under the voice.
5. **Fix and report.** Fix `plan.edl` and render again at most once (`--execute`). Report PASS or FAIL per check in ten lines or fewer, and say what is still wrong; never hide a FAIL line from `verify`. Whatever the check cannot decide (a voice it doubts, a wording difference in the footage) goes to the user.

## Token budget

Agents 1 to 4 and the Director together: aim for 50k, hard border 70k per video. Agent 5: 10k at most. Warn the user as soon as a job looks like passing either.
