# 05 · On-screen text

## Source
- On-screen text comes **only from the script document** (the linked tab).
- If the document does not ask for text, add none.
- Never copy text from the footage's burned-in subtitles.

## Format in the document
**Not defined yet.** The user will give the exact format later. Until then, if a script line seems to contain a text cue, ask the user instead of guessing.

## Rendering
- Text is drawn by Pillow (`ui_generator.py`) and overlaid by FFmpeg.
- Font, size, colour, position and timing come from the active style's `spec.json`.
- Indic scripts need a font that covers them. Check coverage before rendering.
- Existing overlay types: chat bar (`label=`), video-call card, split borders, ✓/✗ marks.
