# Production Feature Audit & Fix Plan

## Goal
Identify all broken production features, root-cause them, fix them, and build a system to prevent regressions.

---

## 🔴 CONFIRMED BROKEN — Known Issues

### 1. Download Recording Not Working

**Root Cause:** `STORAGE_TYPE=local` in `.env.prod` but `.env` (dev) has `STORAGE_TYPE=gcp` with GCP bucket `bifrost-pnyx-storage-b50ae8c5`.

**What happens in production:**

1. Audio is recorded and saved during the meeting
2. If production backend runs with `STORAGE_TYPE=local`, files go to `./data/recordings/` **on the container's filesystem**
3. When you click "Download Recording", it calls `GET /meetings/{id}/recording-url`
4. [audio.py:2600](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/api/routers/audio.py#L2600) reads `STORAGE_TYPE` and checks local filesystem
5. For local storage, `generate_signed_url()` returns `/audio/{path}` — but **there's no StaticFiles mount for `/audio/`** in `main.py` or any router!
6. Even if recordings exist on disk, there's no HTTP route to serve them
7. Cross-storage fallback logic (lines 2616-2658) tries the other storage type, but if neither has the file, it 404s

**Multiple compounding issues:**
- `.env.prod` says `STORAGE_TYPE=local` but `.env` says `STORAGE_TYPE=gcp` — confusion about which is actually deployed
- No FastAPI `StaticFiles` mount exists to serve `/audio/` prefix for local recordings
- Docker `data/` volume may not persist between deployments
- GCP bucket name differs between `.env` and `.env.prod` (`bifrost-pnyx-storage-b50ae8c5` vs `pnyx-storage`)

---

### 2. "Start Pnyx from Email" Not Working in Production

**Root Cause:** Double-slash URL bug in `.env.prod`:

```
CALENDAR_EMAIL_START_MEETING_URL=https://pnyxx.vercel.app//?autoStart=true&source=calendar_email
```

Note the `//` after the domain — this creates URL `https://pnyxx.vercel.app//?autoStart=true&source=calendar_email&meetingTitle=...`.

**Why it fails:**

1. The email is sent correctly by [reminder_email.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/services/calendar/reminder_email.py#L15)
2. User clicks the link and lands on `https://pnyxx.vercel.app//` — the double-slash may cause issues:
   - Vercel/Next.js may normalize `//` to `/` and **strip the query params** in the process
   - Even if it works, `window.location.search` sees `?autoStart=true`
3. The auto-start effect ([page.tsx:1669-1709](file:///home/gagansharma/Projects/meeting-co-pilot/frontend/src/app/page.tsx#L1669)) checks `urlParams.get('autoStart') === 'true'`
4. But **it also requires `!isRecording && !isMeetingActive`** and the user must be **logged in** (auth session required)
5. If the user isn't already logged in when clicking the email link, they get redirected to `/login`, and the `?autoStart=true` query param is **lost** during the auth redirect flow

**Additional issues:**
- The `CALENDAR_OAUTH_FRONTEND_SETTINGS_URL` also has a double-slash: `https://pnyxx.vercel.app//settings`
- Calendar reminder scheduling uses the backend's timezone, but emails are hardcoded to IST

---

## 🟠 HIGH RISK — Likely Broken in Production

### 3. Celery Beat Not in Production Docker Compose

> [!CAUTION]
> The `docker-compose.prod.yml` has **no Celery Beat service**, only a worker. The dev `docker-compose.yml` has one (line 134).

**Impact:** The weekly credit reset (`crontab(hour=0, minute=0, day_of_week=1)`) **never fires** in production. Users' free weekly credits are never replenished.

**Fix:** Add celery-beat service to `docker-compose.prod.yml`.

---

### 4. Razorpay Payments Non-Functional

```
RAZORPAY_KEY_ID=null
RAZORPAY_KEY_SECRET=null
RAZORPAY_WEBHOOK_SECRET=null
```

Both `.env` and `.env.prod` have Razorpay keys set to literal `null`. The payment system will:
- Fail to create orders (frontend `PurchaseCreditsModal.tsx` calls `/api/payments/create-order`)
- Webhook signature validation will always fail
- Users cannot purchase credits

---

### 5. Recall.ai Bot Webhook URL Points to Ephemeral Tunnel

```
RECALL_WEBHOOK_URL=https://wqsli-49-249-139-122.a.free.pinggy.link/api/bot/webhook
```

This is a **Pinggy free tunnel** URL that:
- Expires within hours
- Is tied to a specific dev machine
- Won't work in production

`.env.prod` has **no Recall config at all** — the bot integration is completely non-functional in production.

---

### 6. Ollama Model Check Hardcoded to `localhost`

[meeting-details/page.tsx:43](file:///home/gagansharma/Projects/meeting-co-pilot/frontend/src/app/meeting-details/page.tsx#L43):
```typescript
const response = await fetch('http://localhost:11434/api/tags');
```

This hardcoded `localhost:11434` Ollama check runs **on every meeting-details page load in production**. It will:
- Always fail silently (CORS or connection refused)
- Generate console errors
- The `checkForGemmaModel` function returns false, preventing Ollama-based auto-generation

**Fix:** Remove or guard behind development check.

---

### 7. ElevenLabs API Key Mismatch Between Envs

| Env | Key |
|---|---|
| `.env` (dev) | `sk_2d40b186755a613da733b885f68a98328b2aa306c7ed5e6f` |
| `.env.prod` | `sk_d1ed4d3ce208ce22377f706b76d47824e4084ec9bd95f9cb` |

The production key may be exhausted/different from the one being tested. Past conversations show repeated ElevenLabs 401 errors. If the prod key has run out of credits, all transcription silently fails.

---

### 8. Audio Chunk Timing Differs Between Envs

| Setting | Dev | Prod |
|---|---|---|
| `AUDIO_CHUNK_DURATION_SECONDS` | 30 | **10** |
| `AUDIO_BUFFER_FLUSH_INTERVAL_SECONDS` | 30 | **5** |

Production has much more aggressive audio chunking (10s vs 30s). This means:
- 3x more Celery tasks per meeting
- More GCP writes, more network I/O
- More likely to overwhelm the single Celery worker
- Could explain recording reliability issues

---

## 🟡 MEDIUM RISK — May Be Broken

### 9. Share Notes Dialog — Potential Domain Mismatch

The share notes feature generates links, but the link domain depends on `window.location.origin`. If the frontend is on `pnyxx.vercel.app` but users expect `meet.quexio.com`, shared links may point to the wrong domain.

---

### 10. Calendar OAuth Redirect URI Mismatch

| Setting | `.env.prod` | Google Console Expected |
|---|---|---|
| `CALENDAR_OAUTH_REDIRECT_URI` | `https://meet.quexio.com/api/calendar/google/callback` | Must match exactly |

If the production backend URL has changed or if Google Console hasn't been updated, OAuth will fail with "redirect_uri_mismatch".

---

### 11. No `/audio/` Static Mount in Production

For local storage mode, `generate_signed_url()` returns `/audio/{path}`. But `main.py` never mounts a `StaticFiles` at `/audio/`. Even in the nginx config, there's only a `location /api/` proxy — no `/audio/` route.

---

### 12. WebSocket URL May Not Work Behind Vercel/Nginx

The frontend config has:
```typescript
wsUrl: 'wss://meet.quexio.com/ws/streaming-audio'
```

But the nginx config only proxies `/api/` to the backend. WebSocket connections to `/ws/streaming-audio` would **not be proxied**. The nginx config needs `proxy_http_version 1.1` and `Upgrade` headers for WebSocket support.

---

## 📋 Complete Feature Matrix — Production Status

| Feature | Status | Issue |
|---|---|---|
| **Login (Google OAuth)** | ✅ Works | Deployed on Vercel |
| **Start Recording** | ✅ Works | WebSocket streaming functional |
| **Live Transcription** | ⚠️ Depends | ElevenLabs key may be exhausted |
| **Save Meeting** | ✅ Works | DB operations functional |
| **View Past Meetings** | ✅ Works | RBAC + DB query |
| **Generate Notes/Summary** | ✅ Works | Gemini/OpenAI integration |
| **Download Recording** | 🔴 Broken | No static file mount + storage mismatch |
| **Start from Email** | 🔴 Broken | Double-slash URL + auth redirect loses params |
| **Calendar Integration** | ⚠️ Partial | OAuth URI may be stale |
| **Calendar Reminders** | ⚠️ Risky | Requires backend running + scheduler |
| **Weekly Credit Reset** | 🔴 Broken | No Celery Beat in prod compose |
| **Purchase Credits** | 🔴 Broken | Razorpay keys are `null` |
| **AI Participant (Host)** | ✅ Works | Uses OpenAI in prod |
| **Chat / Catch-Up** | ✅ Works | Backend RAG system |
| **Share Notes** | ⚠️ Untested | May have domain issues |
| **Diarization** | ⚠️ Untested | Depends on audio availability |
| **Recall.ai Bot** | 🔴 Broken | Webhook URL is ephemeral tunnel |
| **Feedback System** | ✅ Works | Simple CRUD |
| **Settings (API Keys)** | ✅ Works | Encrypted storage |
| **Admin Dashboard** | ✅ Works | Analytics + credit management |
| **Encryption (E2EE)** | ⚠️ Untested | Complex key management |
| **Playback in Browser** | 🔴 Broken | No audio serving route |

---

## Proposed Changes

### Phase A: Fix Broken Features (Immediate)

---

#### [MODIFY] [.env.prod](file:///home/gagansharma/Projects/meeting-co-pilot/backend/.env.prod)
- Fix double-slash in `CALENDAR_EMAIL_START_MEETING_URL`: `https://pnyxx.vercel.app/?autoStart=true&source=calendar_email`
- Fix double-slash in `CALENDAR_OAUTH_FRONTEND_SETTINGS_URL`: `https://pnyxx.vercel.app/settings`
- Set `STORAGE_TYPE=gcp` and correct bucket name
- Add missing Recall.ai config with production webhook URL

---

#### [MODIFY] [docker-compose.prod.yml](file:///home/gagansharma/Projects/meeting-co-pilot/backend/docker-compose.prod.yml)
- Add celery-beat service (copy from dev compose, adapt for prod)

---

#### [MODIFY] [main.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/main.py)
- Mount `StaticFiles` for `/audio/` directory as fallback for local storage mode

---

#### [MODIFY] [page.tsx](file:///home/gagansharma/Projects/meeting-co-pilot/frontend/src/app/meeting-details/page.tsx)
- Guard `localhost:11434` Ollama check behind `isDevelopment` flag

---

#### [MODIFY] [nginx.conf](file:///home/gagansharma/Projects/meeting-co-pilot/backend/nginx.conf)
- Add WebSocket proxy support for `/ws/` paths
- Add `/audio/` proxy or static serving

---

### Phase B: Build Pre-Production Validation System

> [!IMPORTANT]
> This addresses your question: "how can we find all the things which are not working?"

---

#### [NEW] `backend/app/api/routers/health_deep.py`
Deep health check endpoint that verifies:
- Database connectivity + query latency
- Redis connectivity  
- GCP Storage bucket accessibility
- ElevenLabs / Groq API key validity (test auth)
- Celery worker reachability
- External service availability

---

#### [NEW] `backend/tests/integration/test_production_smoke.py`
Automated smoke test suite covering every critical feature path:
1. **Auth**: Create session, verify token
2. **Meeting CRUD**: Create, list, get, delete
3. **Recording URL**: Verify signed URL generation works
4. **Storage**: Upload + download roundtrip
5. **Transcription**: Send test audio, verify response
6. **Notes Generation**: Trigger summary, verify output
7. **Credit System**: Check balance, verify reset logic
8. **Calendar**: OAuth URL generation
9. **Email**: SMTP connection test (no send)
10. **Share Notes**: Create + verify share link

---

#### [NEW] `.github/workflows/pre-deploy.yml`
CI/CD pipeline that runs before every deployment:
1. Lint (ruff for Python, eslint for TypeScript)
2. Unit tests
3. Integration tests against staging DB
4. Build check (Next.js build)
5. Environment variable validation (check all required vars are set and non-empty)
6. Security scan (check no secrets in code)

---

#### [NEW] `scripts/validate_env.py`
Pre-deployment environment validator:
- Checks all required env vars are set
- Validates URLs (no double slashes, correct protocol)
- Validates API keys format (not `null`, not empty)
- Checks CORS origins don't include `localhost` in production
- Verifies GCP credentials file exists and is valid JSON
- Verifies DB connection works
- Outputs clear PASS/FAIL report

---

#### [NEW] `frontend/tests/e2e/critical_paths.spec.ts`
Playwright E2E tests covering:
1. Login flow
2. Start recording → see transcript → stop → save
3. View past meeting → download recording
4. Click email auto-start link → verify recording starts
5. Generate notes → verify notes appear
6. Share notes → open shared link → verify content

---

## Verification Plan

### Automated Tests
```bash
# Run env validation
python scripts/validate_env.py --env .env.prod

# Run deep health check
curl https://meet.quexio.com/health/deep

# Run smoke tests against production
pytest backend/tests/integration/test_production_smoke.py -v

# Run E2E tests
cd frontend && npx playwright test tests/e2e/critical_paths.spec.ts
```

### Manual Verification
After deploying fixes:
1. Click "Start Pnyx" from a calendar reminder email → verify recording auto-starts
2. Complete a meeting → go to meeting details → click Download Recording → verify file downloads
3. Check credit balance → verify it's not stuck at 0 (wait for next Monday reset)
4. Try purchasing credits → verify Razorpay flow (if keys configured)
5. Try inviting a Recall.ai bot → verify it joins and transcribes

---

## Open Questions

> [!IMPORTANT]
> 1. **Which environment file does production actually use?** `.env` or `.env.prod`? The `STORAGE_TYPE` differs between them.
> 2. **Is the production backend deployed via Docker on a VM or Cloud Run?** This affects how we fix the audio serving and volumes.
> 3. **Do you need Razorpay payments working now?** If so, we need real API keys from the Razorpay dashboard.
> 4. **Should we set up Recall.ai with a stable production webhook URL** (e.g., `https://meet.quexio.com/api/bot/webhook`) or disable it for now?
> 5. **Is the frontend deployed on Vercel (pnyxx.vercel.app) and backend on meet.quexio.com?** This split-deployment matters for CORS, WebSocket, and cookie configuration.
