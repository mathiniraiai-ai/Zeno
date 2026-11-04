# Zeno transcription service

One small web service that turns audio into a verbatim transcript with Gemini. The Gemini key lives only here (in Railway); each team member gets a personal key.

- `POST /v1/transcribe` — send a 16 kHz WAV, `language_hint`, `target_script_lines` (JSON list); needs `Authorization: Bearer <personal key>`
- `GET /` signup page, `GET /admin` admin page, `GET /health` (no key needed; shows whether the Gemini key, key store, signup and admin are set)

Audio is processed in memory and never stored. Limits: 25 MB per upload, 20 requests per person per minute (both adjustable).

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
SCRIPTCUT_CLOUD_URL=https://<your-domain>
SCRIPTCUT_CLOUD_TOKEN=zeno_...their personal key...
```
and leaves `GEMINI_API_KEY` empty. Zeno uses the service only when no local Gemini key is set.

## Other settings (Railway variables, all optional)
`ZENO_RATE_PER_MIN`, `ZENO_MAX_UPLOAD_MB`, `GEMINI_RPM`, `GEMINI_MODELS` (model order, default lite first), `GEMINI_API_KEY_1..4` (extra keys).

## Keep the prompt in sync
`service/prompt.py` is a copy of the prompt in `scripts/align_engine/gemini_transcriber.py` (Railway only sees this folder). After changing either, run `python service/check_prompt_sync.py`.

## Tests
`python service/test_keys.py` checks signup, admin, revoke and usage (no Gemini call; add `--database-url postgresql://...` to use a real Postgres).
`python service/test_service.py` starts the service locally and checks health, wrong keys, bad files, size limit, rate limit, and a real transcription through the repo's own client. It makes one Gemini call using the key in your `.env`.
