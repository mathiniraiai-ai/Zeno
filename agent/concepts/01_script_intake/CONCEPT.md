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
- On-screen text cues: **format not defined yet.** The user will give the exact format. Do not guess it.
