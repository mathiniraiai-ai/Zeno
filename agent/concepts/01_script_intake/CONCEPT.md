# 01 · Script intake

Users send the script as a **Google Doc link**. One document holds many translated scripts, so the link points at one tab or heading. Read only that.

## Rules
- Fetch **only the part the link points to**: `?tab=t.…` (a tab) or `#heading=h.…` (a section).
- Never open the full document in your context. Never read other languages.
- The Engine downloads it to disk; you read the small saved file `input/script/<name>.txt`.
- If the link has no tab or heading, the tool saves the whole document and warns. Ask the user which tab, do not read the rest.
- If the document is private (login page returned), ask the user to share the link as "anyone with the link can view". Do not work around it by reading the whole document through a connector.

## Command
```bash
python3 scripts/script_intake.py "<google-doc-link-or-local-file>" --name <project>
```

## Script format (`parse_script_file` in `prepare.py`)
```text
# Title - Topic (Language)
L1 Teacher: spoken text [RIGHT]
L2 Student: spoken text [WRONG]
```
- `.txt`, `.md` and `.docx` files are accepted and normalised (NFC).
- **Note on a line**: `L4 Man (laughs mockingly): text`. The text in brackets is the line's note. A reaction note (laugh, cry, sigh, smirk, shock...) keeps up to 1.2 s of the reaction after the last word instead of trimming it. A note with *pause*, *silence*, *stare* or *awkward* keeps the pause instead of tightening it.
- **Silent beat** (a moment with no dialogue): a line that is only a description, written as `(Man takes out his phone)`, `[passes the phone]`, `*shocked*` or `Beat: he walks in`. Zeno matches it, in order, to a clip with no speech and keeps the most active stretch by picture movement (`scripts/motion.py` shows where): up to 3 s, or about 2 s with a slow push-in for emotional moments (crying, sadness, embarrassment). A beat with no silent clip is reported as `[BEAT_NO_CLIP]`.
- On-screen text cues: **format not defined yet.** The user will give the exact format. Do not guess it.
