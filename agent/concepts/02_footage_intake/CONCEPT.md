# 02 · Footage intake

The Engine analyses raw clips. You never read its raw JSON.

## Pipeline (all inside `prepare.py`)
| Step | Tool |
|---|---|
| Audio, pauses, loudness | FFmpeg (`silencedetect`, `loudnorm`) |
| What was said (verbatim, retakes included) | Gemini Flash (`gemini_transcriber.py`) |
| Exact word times | Meta **MMS_FA** + Indic G2P (`align_engine/`) |
| Faces, framing | OpenCV YuNet (`vision_tracker.py`) |
| Take choice vs script | `difflib` matcher (`script_matcher.py`) |

You read only `work/<project>/brief.txt` and `plan.edl`.

## Subtitles in client footage
- Client footage often has **burned-in subtitles**. **Ignore them for now.**
- Do not read them as a transcript. Do not describe or measure them.
- The transcript comes from the audio only.
- Other on-screen text (titles, labels, prompts) is analysed. See `05_onscreen_text`.
- Subtitle handling will be added later, when the user asks.

## Known limits
- `prepare.py` calls `transcribe_and_align_clip(..., script_path=...)`, but that function has no `script_path` argument. The error is swallowed, so the primary path silently falls back to `transcribe.py`. Not fixed yet.
- Mixed-language footage (English scene plus Malayalam scene): transcribe each scene with its own language. One pass with `ml` skipped the English scene entirely.
- Alignment confidence is not reported yet (every word shows 0.5).
- MMS timing is checked on synthetic voices only, not on real footage.
