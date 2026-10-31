# 06 · Token budget

**Goal:** 15–20k tokens per minute of finished video. Earlier fully-Claude edits ran about 70k.
CLAUDE.md sets the working numbers: target 12–18k, ceiling 20k.

## How
- The Engine does the work. You read `brief.txt` (~900 tokens) and `plan.edl` (~240 tokens), plus one contact-sheet image.
- One script tab, never the whole document.
- A fresh session for each video. Unused connectors off.
- Sonnet 5.5 by default. Opus 5.5 only for learning a new style.
- Warn the user mid-job if retakes or messy footage risk passing 20k per minute.

## End of every job
Print the token table and add a row to `README.md` (format in CLAUDE.md §2).
