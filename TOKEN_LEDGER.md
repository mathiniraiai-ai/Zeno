# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent (`Claude Code` / `Antigravity`) | Model | Clips (`Raw → Final`) | Video Edit Agent Tokens (`In / Out / Total` · `tok/min`) | Gemini Transcription Tokens (Separate `In / Out / Total`) |
|---|---|---|---|---|---|---|
| `2026-11-01 00:12:42` | **`kannada_test`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 17 (`169s → 02:06.54`) | `9,367` in / `5,455` out / **`14,822` total** (`~7,028/min`) | `22,204` in / `7,947` out / **`30,151` total** |
| `2026-11-01 13:01:48` | **`Malayalam_Friends_Ordering_Food_Abroad`** (`ml`) | **Antigravity** | `Gemini 4 Argon` | 18 (`164s → 01:42.13`) | `9,581` in / `9,879` out / **`19,460` total** (`~11,432/min`) | `26,015` in / `6,748` out / **`32,763` total** |
| `2026-11-02 22:17:32` | **`stop_saying_21545`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 5 (`149s → 01:21.80`) | `33,193` in / `24,915` out / **`58,108` total** (`~42,622/min`) | `9,150` in / `13,398` out / **`22,548` total** |

## 2. Latest Job Clip-by-Clip Breakdown — `stop_saying_21545` (`2026-11-02 22:17:32`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `21545 X Stop saying this selfie style - girl on scooter` | `59.5s → 55.5s` | 36 | `L1` | `L1a[0.00-44.70s] + L1b[44.84-47.27s] + L6[8.90-17.22s]` | [RETAKE] 'ಅಂದ್ರಾ?' take 1 (44.70-44.84s dropped; kept take 2 from 44.84s with zoom=1.08); [FILL] L6 (Lady): 21545 X Stop saying this selfie style - girl on scooter 8.90-17.22s spliced in because the wrong character spoke it elsewhere | `2,489` | `815` | **`3,304`** |
| `MISS NOVA FV` | `59.6s → 54.2s` | 140 | `L1` | `L1c[1.41-4.70s] + L1d[8.72-59.59s]` | [PAUSE_SPLIT] 4.45s internal pause (4.48-8.93s tightened to 4.70/8.72s with zoom=1.08) | `2,908` | `10,866` | **`13,774`** |
| `S1` | `10.0s → 9.8s` | 21 | `L1` | `L1e[0.00-9.75s]` | Clean take (pause-snapped) | `1,251` | `880` | **`2,131`** |
| `S2` | `10.0s → 2.6s` | 16 | `L6` | `S6[6.28-8.87s]` | [WRONG_SPEAKER] L6: script says Lady, but Miss Nova speaks 'ನೀವು ಈ ತರ English ಮಾತಾಡಿದ್ರೆ ಜನ ನಿಮ್ಮ ಮಾತನ್ನ ಕೇಳ' -> cut 0.00-6.28s, joined to the next good part | `1,251` | `406` | **`1,657`** |
| `S3` | `10.0s → 8.1s` | 16 | `L7` | `L7[0.00-8.07s]` | Clean take (pause-snapped) | `1,251` | `431` | **`1,682`** |
