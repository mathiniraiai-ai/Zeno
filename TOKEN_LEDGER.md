# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent (`Claude Code` / `Antigravity`) | Model | Clips (`Raw → Final`) | Video Edit Agent Tokens (`In / Out / Total` · `tok/min`) | Gemini Transcription Tokens (Separate `In / Out / Total`) |
|---|---|---|---|---|---|---|
| `2026-11-01 00:12:42` | **`kannada_test`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 17 (`169s → 02:06.54`) | `9,367` in / `5,455` out / **`14,822` total** (`~7,028/min`) | `22,204` in / `7,947` out / **`30,151` total** |
| `2026-11-01 13:01:48` | **`Malayalam_Friends_Ordering_Food_Abroad`** (`ml`) | **Antigravity** | `Gemini 4 Argon` | 18 (`164s → 01:42.13`) | `9,581` in / `9,879` out / **`19,460` total** (`~11,432/min`) | `26,015` in / `6,748` out / **`32,763` total** |
| `2026-11-02 22:17:32` | **`stop_saying_21545`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 5 (`149s → 01:21.80`) | `33,193` in / `24,915` out / **`58,108` total** (`~42,622/min`) | `9,150` in / `13,398` out / **`22,548` total** |
| `2026-11-03 16:49:52` | **`hindi_construction_skit`** (`hi`) | **Antigravity** | `Gemini 4 Argon` | 18 (`153s → 02:00.04`) | `46,865` in / `16,379` out / **`63,244` total** (`~31,611/min`) | `21,770` in / `8,049` out / **`29,819` total** |
| `2026-11-04 13:23:07` | **`t`** (`auto`) | **Claude Code** | `claude-sonnet-5-5` | 5 (`0s → 00:25.57`) | `385,628` in / `251,152` out / **`636,780` total** (`~1,494,204/min`) | `0` in / `0` out / **`0` total** |
| `2026-11-04 13:29:08` | **`gdoc_1eKFVUp22BIv_t_0`** (`auto`) | **Claude Code** | `claude-sonnet-5-5` | 5 (`149s → 01:25.73`) | `1,337,565` in / `391,932` out / **`1,729,497` total** (`~1,210,426/min`) | `9,150` in / `13,398` out / **`22,548` total** |
| `2026-11-04 13:53:16` | **`jobtest`** (`auto`) | **Claude Code** | `claude-sonnet-5-5` | 1 (`0s → 00:02.03`) | `150,313` in / `130,061` out / **`280,374` total** (`~1,121,496/min`) | `0` in / `0` out / **`0` total** |
| `2026-11-05 13:20:00` | **`gdoc_1DXaWMt-2hs-_t_ppr074h4oa37`** (`kn`) | **Antigravity** | `Gemini 4 Argon` | 10 (`134s → 01:41.46`) | `17,683` in / `11,802` out / **`29,485` total** (`~17,436/min`) | `14,860` in / `4,727` out / **`19,587` total** |
| `2026-11-05 13:50:05` | **`gdoc_15VT5VlpidqX_t_0`** (`kn`) | **Claude Code** | `claude-sonnet-5-5` | 14 (`170s → 02:13.72`) | `246,138` in / `68,916` out / **`315,054` total** (`~141,364/min`) | `22,987` in / `13,174` out / **`36,161` total** |
| `2026-11-05 14:24:29` | **`gdoc_149jwRt8pzai_t_d8xozxjkesx5`** (`kn`) | **Antigravity** | `Gemini 3.8 Flash` | 11 (`154s → 01:40.54`) | `11,737` in / `6,787` out / **`18,524` total** (`~11,055/min`) | `16,038` in / `27,420` out / **`43,458` total** |

## 2. Latest Job Clip-by-Clip Breakdown — `gdoc_149jwRt8pzai_t_d8xozxjkesx5` (`2026-11-05 14:24:29`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `LADY DFV` | `19.8s → 0.0s` | 46 | `-` | `dropped` | Clean take (pause-snapped) | `1,752` | `4,777` | **`6,529`** |
| `LADY SFV` | `20.0s → 0.0s` | 146 | `-` | `dropped` | Clean take (pause-snapped) | `1,759` | `10,482` | **`12,241`** |
| `MISS NOVA DFV` | `30.6s → 0.0s` | 64 | `-` | `dropped` | Clean take (pause-snapped) | `2,096` | `8,454` | **`10,550`** |
| `MISS NOVA SFV` | `20.0s → 0.0s` | 146 | `-` | `dropped` | Clean take (pause-snapped) | `951` | `425` | **`1,376`** |
| `S1` | `10.0s → 0.0s` | 14 | `-` | `dropped` | Clean take (pause-snapped) | `1,368` | `346` | **`1,714`** |
| `S2` | `10.0s → 0.0s` | 20 | `-` | `dropped` | Clean take (pause-snapped) | `1,368` | `336` | **`1,704`** |
| `S3` | `3.4s → 0.0s` | 4 | `-` | `dropped` | Clean take (pause-snapped) | `1,202` | `154` | **`1,356`** |
| `S4` | `10.0s → 0.0s` | 21 | `-` | `dropped` | Clean take (pause-snapped) | `1,368` | `626` | **`1,994`** |
| `S5` | `10.0s → 0.0s` | 15 | `-` | `dropped` | Clean take (pause-snapped) | `1,368` | `475` | **`1,843`** |
| `S6` | `10.0s → 0.0s` | 17 | `-` | `dropped` | Clean take (pause-snapped) | `1,438` | `869` | **`2,307`** |
| `S7` | `10.0s → 0.0s` | 29 | `-` | `dropped` | Clean take (pause-snapped) | `1,368` | `476` | **`1,844`** |
