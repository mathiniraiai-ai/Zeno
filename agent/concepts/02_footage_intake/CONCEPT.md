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

## Mixed languages
Native language and English are mixed almost every time: single words, phrases, or whole scenes.
- Decide the language **word by word**. An English word stays English (Latin letters), even inside a native sentence. A native word stays native (its own script). Never transliterate one into the other.
- The language given to the Engine is only the *main native language*. The Gemini prompt tells it the audio is mixed and to transcribe every word. Before this was added, a Malayalam hint made Gemini skip a whole English scene.
- Alignment also works word by word: Latin words use English sounds, native words use the Indic mapper, and code-switched compounds such as `English-ലോ` are split.
- Check: after `learn_reference.py align`, `map.json` match values near 1.0 mean no language was dropped. Tested on one Malayalam + English reference (20 lines, 19 match 0.8 or higher).

## Subtitles in client footage
- Client footage often has **burned-in subtitles**. **Ignore them for now.**
- Do not read them as a transcript. Do not describe or measure them.
- The transcript comes from the audio only.
- Other on-screen text (titles, labels, prompts) is analysed. See `05_onscreen_text`.
- Subtitle handling will be added later, when the user asks.

## Known limits
- `prepare.py` calls `transcribe_and_align_clip(..., script_path=...)`, but that function has no `script_path` argument. The error is swallowed, so the primary path silently falls back to `transcribe.py`. Not fixed yet.
- Footage is almost never one language (see "Mixed languages" above).
- Alignment confidence is not reported yet (every word shows 0.5).
- MMS timing is checked on synthetic voices only, not on real footage.
