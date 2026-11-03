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

## 2. Latest Job Clip-by-Clip Breakdown — `hindi_construction_skit` (`2026-11-03 16:49:52`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `M1` | `6.0s → 6.5s` | 21 | `L13` | `L13[0.00-6.52s]` | Clean take (pause-snapped) | `1,148` | `335` | **`1,483`** |
| `R1` | `10.0s → 10.4s` | 25 | `L14,L15,L16` | `L14[0.00-10.37s]` | Clean take (pause-snapped) | `1,248` | `652` | **`1,900`** |
| `1.1` | `8.0s → 7.5s` | 29 | `L17` | `L17a[0.31-7.82s]` | Clean take (pause-snapped) | `1,199` | `523` | **`1,722`** |
| `1.2` | `6.0s → 0.0s` | 2 | `-` | `unused take` | [LISTENING_CLIP] 1.2 detected as silent listening reaction clip (auto-paired via peer=);   unused take would keep 0.40-4.59s of C:\Users\SCL\Documents\Zeno\input\hindi_construction_skit\clips\1.2.mp4 | `1,148` | `123` | **`1,271`** |
| `1.2VO` | `6.0s → 5.8s` | 16 | `L2` | `L2[0.00-5.76s]` | Clean take (pause-snapped) | `1,148` | `317` | **`1,465`** |
| `1.3` | `8.0s → 7.4s` | 31 | `L17` | `L17b[0.56-7.94s]` | Clean take (pause-snapped) | `1,199` | `542` | **`1,741`** |
| `2` | `10.0s → 9.3s` | 34 | `L4` | `L4[0.00-9.29s]` | [PACING_PASS] 2 (9.29s > 6.0s): added dynamic zoom motion so shot never stays static | `1,248` | `660` | **`1,908`** |
| `M2` | `8.0s → 8.0s` | 31 | `L18` | `L18a[0.00-8.00s]` | Clean take (pause-snapped) | `1,199` | `573` | **`1,772`** |
| `R2` | `10.0s → 8.7s` | 31 | `L18` | `L18b[0.98-9.72s]` | [HEAD_REPEAT] 'कोई बात नहीं!' (0.00-0.94s false-start cut at 0.98s) | `1,248` | `615` | **`1,863`** |
| `3` | `6.0s → 4.0s` | 10 | `L7` | `L7[0.29-4.30s]` | Clean take (pause-snapped) | `1,148` | `153` | **`1,301`** |
| `R3` | `10.0s → 9.6s` | 31 | `L20` | `L20[0.00-9.64s]` | Clean take (pause-snapped) | `1,248` | `575` | **`1,823`** |
| `r4` | `10.0s → 9.7s` | 25 | `L21,L22` | `L21a[0.00-9.72s]` | Clean take (pause-snapped) | `1,248` | `447` | **`1,695`** |
| `4.1` | `8.0s → 7.2s` | 31 | `L21` | `L21b[0.80-7.97s]` | [HEAD_BLEED_CUT] 'Wow!' (0.24-0.74s cut at 0.80s) | `1,199` | `581` | **`1,780`** |
| `4.2` | `10.0s → 10.0s` | 36 | `L21` | `L21c[0.00-10.03s]` | Clean take (pause-snapped) | `1,248` | `654` | **`1,902`** |
| `4.3` | `10.0s → 10.0s` | 38 | `L21` | `L21d[0.00-10.03s]` | Clean take (pause-snapped) | `1,248` | `516` | **`1,764`** |
| `5` | `10.0s → 9.3s` | 30 | `L21` | `L21e[0.00-9.29s]` | Clean take (pause-snapped) | `1,248` | `564` | **`1,812`** |
| `M extra` | `8.0s → 0.0s` | 1 | `-` | `unused take` | [LISTENING_CLIP] M extra detected as silent listening reaction clip (auto-paired via peer=); [TAIL_BABBLE_CUT] trimmed at 1.10s (untranscribed tail audio 1.55-8.04s);   unused take would keep 0.00-1.10s of C:\Users\SCL\Documents\Zeno\input\hindi_construction_skit\clips\M extra.mp4 | `1,199` | `110` | **`1,309`** |
| `R extra` | `8.0s → 0.0s` | 1 | `-` | `unused take` | [LISTENING_CLIP] R extra detected as silent listening reaction clip (auto-paired via peer=); [PACING_PASS] R extra (7.00s > 6.0s): added dynamic zoom motion so shot never stays static;   unused take would keep 0.21-7.21s of C:\Users\SCL\Documents\Zeno\input\hindi_construction_skit\clips\R extra.mp4 | `1,199` | `109` | **`1,308`** |
