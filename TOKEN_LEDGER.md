# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent (`Claude Code` / `Antigravity`) | Model | Clips (`Raw → Final`) | Video Edit Agent Tokens (`In / Out / Total` · `tok/min`) | Gemini Transcription Tokens (Separate `In / Out / Total`) |
|---|---|---|---|---|---|---|
| `2026-11-01 00:12:42` | **`kannada_test`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 17 (`169s → 02:06.54`) | `9,367` in / `5,455` out / **`14,822` total** (`~7,028/min`) | `22,204` in / `7,947` out / **`30,151` total** |

## 2. Latest Job Clip-by-Clip Breakdown — `kannada_test` (`2026-11-01 00:12:42`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `S1` | `10.0s → 8.8s` | 48 | `L1,L2,L3,L4` | `L1[0.00-3.75s] + L4[4.20-9.25s]` | [SPEAKER_CHECK] not applied: too few measurable phrases per speaker {'L1': 1, 'L2': 2, 'L3': 3, 'L5': 2, 'L6': 2, 'L7': 1, 'L8': 1, 'L9': 2, 'L10': 1, 'L11': 1, 'L12': 1, 'L13': 1, 'L14': 1, 'L15': 2, 'L16': 1, 'L17': 1}; [BEST_TAKE] L1,L2,L3,L4: kept S1 of 3 takes (S1 93% said/100% on-script, S3 40% said/100% on-script, S4 44% said/100% on-script); [RETAKE] 'Almost,' take 1 (3.75-4.20s dropped; kept take 2 from 4.20s with zoom=1.08) | `1,309` | `1,136` | **`2,445`** |
| `S2` | `10.0s → 3.5s` | 12 | `L2` | `L2a[0.45-1.72s] + L2b[3.32-5.55s]` | [TAIL_REPEAT] 'Do you want coffee?' (6.51-7.36s cut at 5.55s); [PAUSE_SPLIT] 1.98s internal pause (1.51-3.49s tightened to 1.72/3.32s with zoom=1.08) | `1,309` | `371` | **`1,680`** |
| `S3` | `10.0s → 0.0s` | 11 | `L1,L2,L3,L4` | `unused take` | [ALT_TAKE] L1,L2,L3,L4: not used, S1 is the best of 3 (S1 93% said/100% on-script, S3 40% said/100% on-script, S4 44% said/100% on-script). To use this take instead, put S3 in that line's segment in plan.edl; [TAIL_BABBLE_CUT] trimmed at 6.28s (untranscribed tail audio 6.53-10.03s);   unused take would keep 0.00-6.28s of /Users/mathiniraiselvan/Documents/Office project/Zeno/input/footage/kannada_test/RAW/S3.mp4 | `1,309` | `307` | **`1,616`** |
| `S4` | `10.0s → 0.0s` | 11 | `L1,L2,L3,L4` | `unused take` | [ALT_TAKE] L1,L2,L3,L4: not used, S1 is the best of 3 (S1 93% said/100% on-script, S3 40% said/100% on-script, S4 44% said/100% on-script). To use this take instead, put S4 in that line's segment in plan.edl; [TAIL_BABBLE_CUT] trimmed at 5.98s (untranscribed tail audio 6.67-10.03s);   unused take would keep 0.62-5.98s of /Users/mathiniraiselvan/Documents/Office project/Zeno/input/footage/kannada_test/RAW/S4.mp4 | `1,309` | `325` | **`1,634`** |
| `S5` | `10.0s → 9.6s` | 16 | `L5` | `L5[0.00-9.63s]` | Clean take (pause-snapped) | `1,309` | `432` | **`1,741`** |
| `S6` | `10.0s → 7.8s` | 12 | `L6` | `L6[0.00-7.82s]` | Clean take (pause-snapped) | `1,309` | `388` | **`1,697`** |
| `S7` | `10.0s → 7.5s` | 12 | `L7` | `L7[0.95-8.48s]` | Clean take (pause-snapped) | `1,309` | `336` | **`1,645`** |
| `S8` | `10.0s → 8.9s` | 20 | `L8` | `L8[0.82-9.75s]` | [BOUNDARY_DUP] 'ಹೀಗೆನೇ' (0.00-0.46s duplicates S7 tail 'ಹೀಗೇನೇ.') | `1,309` | `557` | **`1,866`** |
| `S9` | `10.0s → 6.8s` | 18 | `L9` | `L9a[0.82-3.02s] + L9b[3.98-8.55s]` | [PAUSE_SPLIT] 1.39s internal pause (2.80-4.19s tightened to 3.02/3.98s with zoom=1.08) | `1,309` | `435` | **`1,744`** |
| `S10` | `10.0s → 7.1s` | 16 | `L10` | `L10[0.68-7.75s]` | [TAIL_BABBLE_CUT] trimmed at 7.75s (untranscribed tail audio 8.12-10.03s) | `1,309` | `374` | **`1,683`** |
| `S11` | `10.0s → 10.0s` | 21 | `L11` | `L11[0.00-9.96s]` | Clean take (pause-snapped) | `1,309` | `554` | **`1,863`** |
| `S12` | `10.0s → 9.1s` | 16 | `L12` | `L12[0.00-9.05s]` | Clean take (pause-snapped) | `1,309` | `501` | **`1,810`** |
| `S13` | `8.0s → 2.5s` | 6 | `L13` | `L13[1.75-4.30s]` | Clean take (pause-snapped) | `1,260` | `204` | **`1,464`** |
| `S14` | `10.0s → 9.9s` | 27 | `L14` | `L14[0.00-9.93s]` | Clean take (pause-snapped) | `1,309` | `608` | **`1,917`** |
| `S15` | `10.0s → 9.6s` | 20 | `L15` | `L15[0.30-9.88s]` | Clean take (pause-snapped) | `1,309` | `560` | **`1,869`** |
| `S16` | `10.0s → 7.9s` | 16 | `L16` | `L16[1.05-8.98s]` | Clean take (pause-snapped) | `1,309` | `384` | **`1,693`** |
| `S17` | `10.0s → 9.6s` | 22 | `L17` | `L17[0.25-9.80s]` | Clean take (pause-snapped) | `1,309` | `475` | **`1,784`** |
