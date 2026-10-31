# 03 · Style library

A **style** is one kind of video (for example vertical split, video call, scrolling text). Each style is a folder in `styles/`.

## Folder layout
```text
styles/<style-name>/
  SKILL.md        # when to use it + the rules, under 60 lines
  spec.json       # measured numbers: sizes, positions, zoom, fonts, colours, timings
  map.json        # reference script lines <-> timestamps
  refs/           # keyframes, contact_sheet.jpg, notes on the reference clip
```

## Naming
`<layout>--<purpose>`, lowercase, for example `vertical-split--roleplay`, `video-call--dialogue`.
- The user names the video type. If they don't, take it from the script header and layout, then confirm with the user.
- One name per style. Do not create a second folder for the same thing.

## Rules
- Numbers in `spec.json` come from a reference video, never from assumptions.
- `SKILL.md` stays thin: it says *what* to do and points to `spec.json` for *how much*.
- A style may allow things the global rules forbid (captions, extra graphics). Its `SKILL.md` must say so explicitly.
- Do not build the style router until two real styles exist.
