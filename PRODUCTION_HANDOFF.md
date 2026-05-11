# Production Handoff — What YOU Must Do

This doc lists every action required from a human before you can ship safely.
Code-level fixes from `Claude-Vulnerablities-Audit.md` and
`Claude-Feature-Audit.md` are already applied. The items below cannot be
fixed in code — they need credentials, infrastructure access, or org policy.

Severity legend: 🔴 do before next deploy · 🟠 do this week · 🟡 do this month.

---

## 🔴 1. Rotate every secret that touched a developer machine

Even though `.env`, `.env.prod`, and `gcp-service-account.json` are listed in
`.gitignore`, the GCP service account WAS committed in commit `3af770e` and
remains in git history. Treat **every** secret in those files as exposed.

**Do this once, in order:**

1. Open each provider console and rotate / regenerate:
   - Google OAuth Client Secret (`GOOGLE_CLIENT_SECRET`,
     `CALENDAR_GOOGLE_CLIENT_SECRET`)
   - GCP service account → delete the leaked key, create a new one, download
     fresh `gcp-service-account.json`
   - OpenAI, Anthropic, Gemini, Groq, ElevenLabs (×2), Deepgram, Tavily,
     SerpAPI, Pyonnate, Recall.ai
   - Neon Postgres (`DATABASE_URL`) — rotate the role password
   - Gmail App Password (`SMTP_PASSWORD`) — revoke + create new
   - `MASTER_KEY` — see warning below
   - `NEXTAUTH_SECRET`
   - Razorpay (when you actually wire payments back on)
2. Update `backend/.env.prod` and `frontend/.env.local` with the new values.
3. Restart all services — they must reload env vars.
4. Purge git history of the leaked file:
   ```bash
   pip install git-filter-repo
   git filter-repo --path backend/gcp-service-account.json --invert-paths
   git push --force-with-lease origin main
   ```
   ⚠️ Force-push rewrites history. Coordinate with anyone else with clones.

**⚠️ MASTER_KEY caveat:** rotating it will make every encrypted-at-rest
record (per-user API keys, encrypted recordings, summaries) un-readable.
Either:
- accept the data loss (acceptable if no real users yet), OR
- write a one-shot migration that decrypts with the old key + re-encrypts
  with the new one before swapping.

---

## 🔴 2. Move secrets out of .env files

Long-term, `.env` files on disk are a foot-gun (one accidental `git add -A`
and they're back in history). Migrate to **GCP Secret Manager**:

1. Store each secret as a Secret Manager entry.
2. Grant the runtime service account `roles/secretmanager.secretAccessor`.
3. Inject into the container at startup — either:
   - via Cloud Run's `--set-secrets` flag, OR
   - via a small loader at the top of `app/main.py` that reads
     `os.environ.update(load_from_secret_manager(...))` before any other
     module imports.

Track which secret rotates which provider in a runbook.

---

## 🔴 3. Verify rate-limit storage in production

`slowapi` defaults to in-process memory. With Gunicorn running **4 workers**,
each worker tracks its own counters, so the effective limit is 4× what's
configured. Set a Redis backend for shared counters:

```
RATELIMIT_STORAGE=redis://redis:6379/2
```

Already documented in `backend/.env.prod` and `backend/.env.example`.

---

## 🔴 4. Re-set AUDIO_SIGNING_KEY (separate from MASTER_KEY)

The `/audio/signed/{token}` endpoint uses HMAC tokens to let `<audio src=>`
work without an Authorization header. By default it falls back to
`MASTER_KEY`. For better isolation, generate a dedicated key:

```bash
openssl rand -base64 48
```

Set `AUDIO_SIGNING_KEY` in `.env.prod`. Rotating this only invalidates
in-flight playback URLs, not stored data.

---

## 🟠 5. Wire payments + bots when you actually need them

Razorpay and Recall.ai are intentionally left disabled (keys are `null` /
commented out). Validator + UI surfaces this clearly. When you're ready:

**Razorpay** (`backend/.env.prod`):
- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
- Configure the webhook URL in Razorpay dashboard:
  `https://meet.quexio.com/api/payments/webhook`
- Test with Razorpay test mode first.

**Recall.ai** (`backend/.env.prod`):
- `RECALL_API_KEY`, `RECALL_WEBHOOK_SECRET`
- `RECALL_WEBHOOK_URL=https://meet.quexio.com/api/bot/webhook`
- DO NOT use the Pinggy tunnel URL in production — it expires within hours.

---

## 🟠 6. Configure Sentry DSNs (now wired in code)

Backend (`backend/.env.prod`):
```
SENTRY_DSN=...
SENTRY_TRACES_SAMPLE_RATE=0.05
RELEASE_VERSION=$(git rev-parse --short HEAD)
```

Frontend — set in Vercel (Project Settings → Environment Variables):
```
NEXT_PUBLIC_SENTRY_DSN=...
NEXT_PUBLIC_SENTRY_ENVIRONMENT=production
NEXT_PUBLIC_SENTRY_RELEASE=...
```

Without DSNs, Sentry init is a no-op — no errors, just no telemetry.

---

## 🟠 7. Verify Google OAuth Console matches deployed URLs

In **Google Cloud Console → APIs & Services → Credentials**, confirm the
Calendar OAuth client lists exactly these as authorized redirect URIs:

```
https://meet.quexio.com/api/calendar/google/callback
```

…and **only** these. Remove any leftover localhost / vercel preview URLs.

---

## 🟠 8. Set up Neon database backups

Neon has point-in-time recovery built in but it's tier-dependent. Verify:

1. Neon dashboard → Branches → confirm a backup retention period is set.
2. Document the restore procedure in your runbook.
3. Test it once: spin up a branch from a backup, run a sanity query.

If Neon's tier doesn't include enough retention, schedule a nightly
`pg_dump` to GCS via a Celery Beat task.

---

## 🟠 9. Deploy Celery Beat (now in docker-compose.prod.yml)

The new `celery-beat` service in `backend/docker-compose.prod.yml` runs the
weekly credit-reset cron. After pulling these changes:

```bash
cd backend
docker-compose -f docker-compose.prod.yml up -d celery-beat
docker logs meeting-copilot-celery-beat --tail=50
```

Confirm you see a "beat: Starting..." line. Mondays at 00:00 UTC the credit
reset job will fire. Check next Monday morning.

---

## 🟠 10. Apply the new nginx config

`backend/nginx.conf` now proxies `/ws/` (WebSockets) and `/audio/` (signed
recordings). Restart nginx to pick this up:

```bash
docker-compose -f docker-compose.prod.yml restart web-ui
```

Then verify from the browser console:
- `wss://meet.quexio.com/ws/streaming-audio` connects (start a recording)
- `https://meet.quexio.com/audio/signed/<token>` returns 200 (download a
  recording on a meeting that has one stored locally)

---

## 🟠 11. Run the env validator in CI

The new `.github/workflows/pre-deploy.yml` includes an `env-validation`
job, but it only runs if `backend/.env.prod` is in the repo (which it
shouldn't be long-term — see item 2). For now, run manually before any
deploy:

```bash
python scripts/validate_env.py --env backend/.env.prod
```

Exit code 1 → don't deploy.

---

## 🟡 12. Add real test coverage

Coverage today: 4 backend unit + 5 backend integration + 1 Playwright
smoke. Write tests for:
- Auth + RBAC (must-have — security regressions are silent)
- Payments webhook signature validation
- Calendar OAuth state validation
- Audio signed-URL HMAC
- /chat-meeting + /catch-up + /refine-notes happy paths
- Frontend recording control state machine

Target: 70% line coverage on `backend/app/api/routers/` and
`backend/app/core/`.

---

## 🟡 13. Split the monoliths

`audio.py` (3,415 lines) and `transcripts.py` (2,396 lines) are review
hazards. Suggested splits (do this gradually as you touch them):

- `audio.py` → `audio/streaming.py`, `audio/recording.py`,
  `audio/artifacts.py`, `audio/sessions.py`, `audio/integrity.py`
- `transcripts.py` → `transcripts/crud.py`, `transcripts/notes.py`,
  `transcripts/refinement.py`, `transcripts/versions.py`

Don't rewrite — just relocate route handlers to new files and re-export.

---

## 🟡 14. Add a real database migration tool

Today: raw `.sql` files + ad-hoc Python scripts in `backend/app/migrations/`.
Two `014_*.sql` files exist (duplicate number). Add Alembic:

```bash
cd backend
pip install alembic
alembic init -t async migrations_alembic
```

Backfill the existing schema as the initial revision, then write all future
DDL through Alembic.

---

## 🟡 15. Re-enable React Strict Mode

`frontend/next.config.js` has `reactStrictMode: false` because of
BlockNote. Track upstream — newer BlockNote releases may fix the issue.
Once they do, flip it back to `true` to surface latent bugs.

---

## 🟡 16. ElevenLabs key rotation + monitoring

The audit noted past 401 errors. Either:
- Top up the prod ElevenLabs subscription, OR
- Set `TRANSCRIPTION_PROVIDER=groq` in `.env.prod` (Groq is cheaper and
  the streaming pipeline already supports it).

The new `/health/deep` endpoint reports ElevenLabs auth status — wire it
to your alerting so you know within minutes when the key dies again.

---

## What's already fixed in code (you don't need to do these)

| Item | Where |
|------|-------|
| XSS sanitization on `dangerouslySetInnerHTML` | `RefineNotesSidebar.tsx`, `notes/[id]/page.tsx` |
| Rate limiting middleware (slowapi) | `backend/app/core/rate_limit.py` + wired in `main.py` |
| Security headers (CSP, HSTS, X-Frame-Options, ...) | `backend/app/core/security_headers.py`, `frontend/next.config.js` |
| Env-driven CORS, scoped methods/headers | `backend/app/main.py` |
| `/analytics/track` no longer trusts client `user_id` | `backend/app/api/routers/analytics.py` |
| `user_filter` SQL input validation | `backend/app/api/routers/analytics.py` |
| DEBUG-style auth logs removed, error details no longer leak | `backend/app/core/security.py`, `backend/app/api/deps.py` |
| Signed-URL `/audio/signed/{token}` route for local recordings | `backend/app/main.py`, `backend/app/services/audio/signed_urls.py` |
| `.env.prod` double-slashes + STORAGE_TYPE fixed | `backend/.env.prod` |
| Celery Beat in production compose | `backend/docker-compose.prod.yml` |
| Localhost Ollama check guarded | `frontend/src/app/meeting-details/page.tsx` |
| nginx WebSocket + `/audio/` proxy | `backend/nginx.conf` |
| Sentry SDK wired (backend + frontend) | `backend/app/main.py`, `frontend/sentry.*.config.ts` |
| `/health/deep` deep health check | `backend/app/api/routers/health_deep.py` |
| Pre-deploy env validator | `scripts/validate_env.py` |
| GitHub Actions CI workflow | `.github/workflows/pre-deploy.yml` |
| React Error Boundary | `frontend/src/components/ErrorBoundary.tsx` |
| Prod console.log stripping | `frontend/next.config.js` |
| `.env.example` templates | `backend/.env.example`, `frontend/.env.local.example` |

---

## How to verify after deploy

```bash
# 1. Validate env file
python scripts/validate_env.py --env backend/.env.prod

# 2. Deep health check (should return overall=ok, all components ok)
curl https://meet.quexio.com/health/deep | jq

# 3. Hit an endpoint without a token — should NOT 500, should return 401/403
curl -i https://meet.quexio.com/get-meetings

# 4. Confirm security headers
curl -sI https://meet.quexio.com/health | grep -iE "x-frame-options|x-content-type|strict-transport|content-security"

# 5. Trigger a 429 to confirm rate limit is enforced
for i in {1..400}; do curl -s -o /dev/null https://meet.quexio.com/health; done
curl -i https://meet.quexio.com/health   # should be 429

# 6. Start a recording → verify WebSocket through nginx works
# (open the app in a browser and start a meeting)

# 7. Stop a recording → click Download → verify the audio downloads
# (this exercises the new signed-URL flow if STORAGE_TYPE=local, or GCP otherwise)
```
