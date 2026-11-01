# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent (`Claude Code` / `Antigravity`) | Model | Clips (`Raw → Final`) | Video Edit Agent Tokens (`In / Out / Total` · `tok/min`) | Gemini Transcription Tokens (Separate `In / Out / Total`) |
|---|---|---|---|---|---|---|
| `2026-11-01 00:12:42` | **`kannada_test`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 17 (`169s → 02:06.54`) | `9,367` in / `5,455` out / **`14,822` total** (`~7,028/min`) | `22,204` in / `7,947` out / **`30,151` total** |
| `2026-11-01 13:01:48` | **`Malayalam_Friends_Ordering_Food_Abroad`** (`ml`) | **Antigravity** | `Gemini 4 Argon` | 18 (`164s → 01:42.13`) | `9,581` in / `9,879` out / **`19,460` total** (`~11,432/min`) | `26,015` in / `6,748` out / **`32,763` total** |

## 2. Latest Job Clip-by-Clip Breakdown — `Malayalam_Friends_Ordering_Food_Abroad` (`2026-11-01 13:01:48`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `1` | `8.0s → 2.2s` | 5 | `L1` | `L1[1.32-3.54s]` | [SPEAKER_CHECK] not applied: too few measurable phrases per speaker {'Woman': 7, 'Counter Lady': 1, 'Friend': 4, 'Miss Nova': 7}; [BEST_TAKE] L1: kept 1 of 2 takes (1 100% said/100% on-script, 5 60% said/75% on-script) | `1,475` | `161` | **`1,636`** |
| `2` | `8.0s → 3.2s` | 6 | `L2` | `L2[0.74-3.90s]` | Clean take (pause-snapped) | `1,475` | `124` | **`1,599`** |
| `3` | `8.0s → 3.4s` | 10 | `L3` | `L3[0.84-4.22s]` | Clean take (pause-snapped) | `1,475` | `226` | **`1,701`** |
| `4` | `8.0s → 5.3s` | 12 | `L4` | `L4[0.88-6.23s]` | Clean take (pause-snapped) | `1,475` | `199` | **`1,674`** |
| `5` | `8.0s → 0.0s` | 4 | `L1` | `unused take` | [ALT_TAKE] L1: not used, 1 is the best of 2 (1 100% said/100% on-script, 5 60% said/75% on-script). To use this take instead, put 5 in that line's segment in plan.edl; [TAIL_BABBLE_CUT] trimmed at 6.31s (untranscribed tail audio 6.91-8.00s); [PAUSE_SPLIT] 2.55s internal pause (2.02-4.57s tightened to 2.24/4.36s with zoom=1.08);   unused take would keep 0.29-6.31s of /Users/mathiniraiselvan/Downloads/Zeno Testing/drive-download-20261101T060430Z-1-001/5.mp4 | `1,475` | `192` | **`1,667`** |
| `6` | `8.0s → 4.8s` | 1 | `L5` | `L5[0.00-4.82s]` | Clean take (pause-snapped) | `1,339` | `363` | **`1,702`** |
| `7` | `8.0s → 0.0s` | 1 | `L6` | `dropped` | [BOUNDARY_DUP] '(laughter)' (0.00-4.70s duplicates 6 tail '(laughter)') | `566` | `48` | **`614`** |
| `8` | `10.0s → 9.8s` | 26 | `L7,L8` | `L7[0.00-9.83s]` | Clean take (pause-snapped) | `1,526` | `489` | **`2,015`** |
| `9` | `8.0s → 8.0s` | 18 | `L8` | `L8[0.00-8.00s]` | Clean take (pause-snapped) | `1,475` | `564` | **`2,039`** |
| `10` | `10.0s → 8.3s` | 19 | `L9,L10` | `L9[0.00-8.30s]` | Clean take (pause-snapped) | `1,526` | `528` | **`2,054`** |
| `11` | `10.0s → 8.8s` | 17 | `L11,L12` | `L11[0.00-8.75s]` | Clean take (pause-snapped) | `1,526` | `489` | **`2,015`** |
| `12` | `10.0s → 9.0s` | 18 | `L13,L14` | `L13[0.00-8.99s]` | Clean take (pause-snapped) | `1,526` | `357` | **`1,883`** |
| `13` | `10.0s → 9.3s` | 22 | `L15` | `L15[0.35-9.69s]` | Clean take (pause-snapped) | `1,526` | `467` | **`1,993`** |
| `14` | `10.0s → 7.4s` | 15 | `L16` | `L16a[0.31-7.68s]` | Clean take (pause-snapped) | `1,526` | `373` | **`1,899`** |
| `15` | `10.0s → 9.2s` | 19 | `L16` | `L16b[0.46-9.62s]` | Clean take (pause-snapped) | `1,526` | `420` | **`1,946`** |
| `16` | `10.0s → 10.0s` | 32 | `L17,L18` | `L17[0.00-10.01s]` | [BEST_TAKE] L17,L18: kept 16 of 2 takes (16 99% said/99% on-script, 17 93% said/100% on-script) | `1,526` | `699` | **`2,225`** |
| `17` | `10.0s → 0.0s` | 27 | `L17,L18` | `unused take` | [ALT_TAKE] L17,L18: not used, 16 is the best of 2 (16 99% said/99% on-script, 17 93% said/100% on-script). To use this take instead, put 17 in that line's segment in plan.edl;   unused take would keep 0.00-9.69s of /Users/mathiniraiselvan/Downloads/Zeno Testing/drive-download-20261101T060430Z-1-001/17.mp4 | `1,526` | `411` | **`1,937`** |
| `18` | `10.0s → 9.9s` | 30 | `L19,L20` | `L19[0.00-9.91s]` | Clean take (pause-snapped) | `1,526` | `638` | **`2,164`** |
