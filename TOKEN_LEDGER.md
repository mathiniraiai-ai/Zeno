# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
The latest record per output is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`). Historical rows retain their original inline labels.

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent | Model | Clips (`Raw → Final`) | Agent Tokens (uncached / cached / output / total) | Transcription Tokens (new / reused; unknown where unavailable) |
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
| `2026-11-05 20:01:21` | **`gdoc_16Xwq5WJhSlr_t_rm6a9obxu5e9`** (`te`) | **Antigravity** | `Gemini 4 Argon` | 12 (`235s → 02:46.60`) | `2,078` in / `3,514` out / **`5,592` total** (`~2,014/min`) | `22,092` in / `13,985` out / **`36,077` total** |
| `2026-11-07 20:23:30` | **`gdoc_149jwRt8pzai_t_d8xozxjkesx5`** (`auto`) | **Claude Code** | `claude-sonnet-5-5` | 11 (`154s → 01:41.76`) | `132,444` in / `47,564` out / **`180,008` total** (`~106,137/min`) | `12,779` in / `5,171` out / **`17,950` total** |
| `2026-11-16 16:30:47` | **`release_test_v1013`** (`auto`) | **Claude Code** | `claude-fable-5-2` | 23 (`0s → 01:10.25`) | `386,535` in / `41,289` out / **`427,824` total** (`~365,401/min`) | `0` in / `0` out / **`0` total** |
| `2026-11-16 20:22:44` | **`abt_sonnet_wb`** (`auto`) | **Claude Code** | `claude-sonnet-5-5` | 18 (`0s → 01:03.08`) | `114,664` in / `26,105` out / **`140,769` total** (`~133,896/min`) | `0` in / `0` out / **`0` total** |
| `2026-11-16 21:48:08` | **`gdoc_1F3i-288t96m_t_2sl36zopschq`** (`kn`) | **Codex** | `GPT-6.1 Sol` | 12 (102s → 01:17.33) | 115,265 uncached in / 5,132 out / **120,397 uncached in + out**; 2,925,824 cached in; **3,046,221 total** | 22,227 in / 8,084 out / **30,311 prior cached total** |
| `2026-11-16 22:26:26` | **`gdoc_1F3i-288t96m_t_2sl36zopschq`** (`auto`) | **Codex** | `gpt-6.1-sol` | 12 (`102s → 01:17.62`) | `26,903` uncached in / `3,034,496` cached / `3,805` out / **`3,065,204` total including cache** | `0` new / `30,311` reused; 0 unknown clips <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4)/reel_W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4).mp4 --> |
| `2026-11-16 22:49:53` | **`gdoc_1wJjuKQxyOVE_t_rg309xa356jl`** (`kn`) | **Codex** | `gpt-6.1-sol` | 6 (`190s → 02:17.57`) | `55,549` uncached in / `12,085,504` cached / `9,951` out / **`12,151,004` total including cache** | `16,066` new / `0` reused; 0 unknown clips <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/reel_W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek.mp4 --> |
| `2026-11-16 23:28:05` | **`gdoc_1wJjuKQxyOVE_t_rg309xa356jl`** (`auto`) | **Codex** | `gpt-6.1-sol` | 6 (`190s → 02:17.40`) | `52,795` uncached in / `2,675,328` cached / `7,708` out / **`2,735,831` total including cache** | `0` new / `16,066` reused; 0 unknown clips <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/Reel - W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/Edited Reel.mp4 --> |
| `2026-11-17 10:15:21` | **`v1018_w45_test`** (`auto`) | **Antigravity + Claude Direct** | `claude-sonnet-5-5` | 6 (`190s → 02:16.12`) | `16,499` uncached in / `6,396` cached / `22,943` out / **`45,838` total including cache** | `0` new / `16,066` reused; 0 unknown clips <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - v1.0.18 Live 1-Call Test/reel_W45 - v1.0.18 Live 1-Call Test.mp4 --> |

## 2. Latest Job Clip-by-Clip Breakdown — `v1018_w45_test` (`2026-11-17 10:15:21`)

| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |
|---|---|---|---|---|---|---:|---:|---:|
| `LADY SV` | `20.0s → 0.0s` | 0 | `-` | `listener only / unused` | Clean take (pause-snapped) | `0` | `0` | **`0`** |
| `ROBOT SV` | `20.0s → 0.0s` | 0 | `-` | `listener only / unused` | Clean take (pause-snapped) | `0` | `0` | **`0`** |
| `Robot fv` | `105.1s → 99.7s` | 227 | `L4,L6,L8,L10,L12,L14,L16,L19,L21,L23,L25,L27` | `L4a + L4b + L4c + L6 + L8 + L10 + L12 + L14 + L16 + L19 + L21 + L23 + L25 + L27` | Clean take (pause-snapped) | `4,189` | `3,532` | **`7,721`** |
| `S1` | `10.0s → 9.2s` | 28 | `L1,L2` | `L1a + L1b + L2a + L2b` | Clean take (pause-snapped) | `1,811` | `621` | **`2,432`** |
| `S2` | `10.0s → 5.8s` | 15 | `L3` | `L3a + L3b` | Clean take (pause-snapped) | `1,811` | `448` | **`2,259`** |
| `lady fv` | `25.1s → 21.3s` | 61 | `L5,L7,L9,L11,L13,L15,L17,L18,L20,L22,L24,L26` | `L5 + L7 + L9 + L11 + L13 + L15 + L17 + L18 + L20a + L20b + L22 + L24 + L26` | Clean take (pause-snapped) | `2,189` | `1,465` | **`3,654`** |
