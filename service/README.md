# Zeno transcription service

One small web service that turns audio into a verbatim transcript with Gemini. The Gemini key lives only here (in Railway); each team member gets a personal key.

- `POST /v1/transcribe` — send a 16 kHz WAV, `language_hint`, `target_script_lines` (JSON list); needs `Authorization: Bearer <personal key>`
- `GET /` signup page, `GET /admin` admin page, `GET /health` (no key needed; shows whether the Gemini key, key store, signup and admin are set)

Audio is processed in memory and never stored. Limits: 25 MB per upload, 20 requests per person per minute (both adjustable).

**Deploying the server:** from the repo root run `railway up service --path-as-root --detach`. Plain `railway up` (even inside `service/`) uploads the whole repo (about 27 MB), Railway then finds no `app.py` at the top and the build fails with "No start command detected". A good upload is about 60 kB. Pushing to GitHub does not redeploy the server.

## Deploy on Railway (about 10 minutes, no Docker needed)
Dashboard labels can change; follow the idea if a name differs. Railway detects Python from `requirements.txt` and starts the app from `Procfile`.
1. In your existing Railway project, add a **new service from this GitHub repo**.
2. In the service settings, set the **Root Directory to `service`**.
3. Under **Variables**, add:
   - `GEMINI_API_KEY` = your Gemini key (lives only here)
   - `DATABASE_URL` = reference the existing Postgres service's `DATABASE_URL` (Railway's "Add Reference" button)
   - `ZENO_GOOGLE_CLIENT_ID` = the OAuth Web client id (Google Auth Platform); people sign in with Google, only `ZENO_ALLOWED_EMAIL_DOMAINS` (default `gosupernova.live`) is accepted
   - `ZENO_ADMIN_EMAILS` = comma-separated emails that may open `/admin` with their key (company keys are not admin unless `ZENO_ADMIN_ALLOW_DOMAIN=1`)
   - `ZENO_SIGNUP_CODE` = team code for the old name-only signup (`POST /api/keys`). Unset means that signup is off (403); Google sign-in does not need it
   - `ZENO_ADMIN_PASSWORD` = password for `/admin` (admin is off until set)
4. Under **Networking**, generate a public domain. That is the address your team uses.
5. Open the domain: the signup page is at `/`, the admin page at `/admin` (browser asks for the password; any username).
6. Check it: `python service/test_service.py --url https://<domain> --token <a personal key> --no-live`, then once without `--no-live`.

## How people get keys
Open the Zeno card in the hub (it links to the service address), type your name (the `@gosupernova.live` part is fixed, so "Mathinirai Selvan" becomes mathinirai.selvan@gosupernova.live) (and the team code if one is set), click **Generate**. The key is shown once and only its hash is stored in Postgres. The page also shows the two `.env` lines to paste. Only emails at `ZENO_ALLOWED_EMAIL_DOMAIN` (default `gosupernova.live`) are accepted. The email is not verified by sending a message, so it is a filter, not proof of identity. Each email can hold 3 active keys, and at most 30 keys are created per hour overall (`ZENO_MAX_SIGNUPS_PER_HOUR`).
In `/admin` you see everyone, their last use, request count and audio minutes, and can **Revoke** a key; it stops working immediately, no redeploy.
Without `DATABASE_URL` the service uses a local SQLite file (`zeno_keys.db`, git-ignored); fine on your laptop, but Railway's disk is wiped on redeploy, so always set `DATABASE_URL` there.

## Optional: keys by hand
`python service/make_token.py "Asha"` still works. Add the printed line to `ZENO_API_TOKENS` in Railway. These keys have no usage tracking and can't be revoked without a redeploy.

## What each person puts in their own `.env` (never in git)
```
ZENO_CLOUD_URL=https://<your-domain>
ZENO_CLOUD_TOKEN=zeno_...their personal key...
```
*(Or sign in once via `python3 scripts/setup.py --login` or the Zeno Desktop App, which stores `.zeno_session.json` and syncs `.env` automatically.)* Leave `GEMINI_API_KEY` empty — Zeno uses Zeno Cloud whenever no local Gemini key is set.

## Other settings (Railway variables, all optional)
- **Gemini Free Tier pacing (default):** tuned to Google's official per-`(key, model)` sliding 60s windows so concurrent editors queue smoothly without tripping `429` bursts:
  - `GEMINI_TIER=free` (default; set `paid` for Paid Tier limits)
  - `GEMINI_CONCURRENCY` (default on Free Tier: `1` on a single key so editors serialize cleanly in the fair round-robin queue, or `min(keys, 2)` with multiple keys; Paid Tier: `3` on 1 key, up to `8` on multiple keys; clamp `1..16`)
  - `GEMINI_MIN_GAP_SEC=3.0` (minimum buffer in seconds between any two Gemini calls on the same key, even when rotating models; `0.5` on Paid Tier)
  - `GEMINI_FLASH_RPM=5` (per-`(key, model)` 61s sliding-window RPM cap and `12.5s` smooth spacing for Full Flash models; `150` on Paid Tier)
  - `GEMINI_LITE_RPM=15` (per-`(key, model)` 61s sliding-window RPM cap and `4.2s` smooth spacing for Flash-Lite fallback models; `600` on Paid Tier)
  - `GEMINI_TPM=240000` (per-`(key, model)` 61s sliding-window token buffer; `1000000` on Paid Tier)
  - `GEMINI_QUEUE_TIMEOUT=180` (seconds a queued transcription waits for a slot before returning `503`)
  - `GEMINI_RPM` (optional total per-key 61s RPM cap across all models; on Free Tier, also caps Flash/Lite RPM when lower unless `GEMINI_FLASH_RPM`/`GEMINI_LITE_RPM` or `GEMINI_TIER=paid` is explicitly set)
- **Other limits & keys:** `ZENO_RATE_PER_MIN` (default `20`), `ZENO_MAX_UPLOAD_MB` (default `25`), `GEMINI_MODELS` (model order, default `gemini-3.5-flash` full Flash first), `GEMINI_API_KEY_1..4` (extra project keys).

## Keep the prompt in sync
`service/prompt.py` is a copy of the prompt in `scripts/align_engine/gemini_transcriber.py` (Railway only sees this folder). After changing either, run `python service/check_prompt_sync.py`.

## Tests
`python service/test_keys.py` checks signup, admin, revoke and usage (no Gemini call; add `--database-url postgresql://...` to use a real Postgres).
`python service/test_service.py` starts the service locally and checks health, wrong keys, bad files, size limit, rate limit, and a real transcription through the repo's own client. It makes one Gemini call using the key in your `.env`.

## Transcript cache and team formats
- **Transcript cache:** `/v1/transcribe` keys results by the audio's SHA-256, the prompt/model version, the language and the script lines. A repeat (a retry, a second editor on the same clip) is served without calling Gemini and costs no tokens (`"cached": true`). Entries last 30 days; an incomplete transcript is never cached. This stores transcripts server-side, so treat the database as company data.
- **Formats:** `POST /v1/observations` (a computer's key) stores per-format numbers after a job; `GET /admin/api/formats` lists candidates; `POST /admin/api/formats/<style>/<format>/approve` (admin, needs `X-Zeno-Admin: 1`, at least `ZENO_FORMAT_MIN_JOBS` jobs, default 5); `GET /v1/formats` serves approved recipes.
- Pre-job check: `POST /v1/selftest` (see scripts/preflight.py).

## Knowledge in the database (no deploy for new recipes)
`PUT /admin/api/knowledge/<kind>/<name>` (admin, `X-Zeno-Admin: 1`) publishes a format recipe or a reference learning; `GET /v1/knowledge?since=<ts>` returns what is new
(every computer calls it at the start of a job); `POST /v1/match` answers which format a scene wording describes (Gemini embeddings when available, words otherwise;
set `ZENO_EMBED_MODEL` to change the model). Embeddings are stored as JSON and compared on the server, so no database extension is needed. New tables are created in a
protected step: if one fails, sign-in and transcription still start.
