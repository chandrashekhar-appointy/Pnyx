# Pnyx v1 Reliability Plan

**Owner:** gagan@appointy.com  
**Created:** 2026-06-08  
**Status:** Active

---

## Principle

> A reliable v1 is a small v1. Make one user journey bulletproof, fix the broken-but-important pillars (Recall.ai, prod analytics), kill config drift, hide everything else, then lock it down with tests and monitoring.

---

## The Journey That Must Never Break

```
Log in
  → start meeting  (on-site mic  OR  online via Recall.ai bot)
  → see live transcript
  → AI insights / catch-up during meeting
  → stop recording
  → notes & summary generate automatically
  → read notes  |  download recording  |  chat with AI about it
  → credits decrement  (admin can top up any user on request)
```

---

## Phase 0 — Kill Config Drift *(do first, highest leverage)*

**Why:** `.env` and `.env.prod` disagree on critical settings. Prod behaves differently from where you test. This alone explains most "it feels unreliable."

| Issue | Dev | Prod | Fix |
|-------|-----|------|-----|
| `STORAGE_TYPE` | `gcp` | `local` | Align to `gcp` everywhere |
| `AUDIO_CHUNK_DURATION_SECONDS` | 30 | **10** | Use 30 everywhere (10s = 3× Celery load) |
| `AUDIO_BUFFER_FLUSH_INTERVAL_SECONDS` | 30 | **5** | Align |
| Calendar URL | – | `https://pnyxx.vercel.app//` double-slash | Fix |
| GCP bucket name | `bifrost-pnyx-storage-b50ae8c5` | `pnyx-storage` | Align |

**Tasks:**
- [ ] Reconcile `.env` ↔ `.env.prod` — one set of values, documented
- [ ] `scripts/validate_env.py` is already written — make it a **hard gate** in CI (exit 1 = no deploy)
- [ ] Decide audio chunk timing (30s recommended) and use it in both envs
- [ ] Add env validation to `.github/workflows/pre-deploy.yml`

**Deliverable:** One config document. Validator runs before every deploy. Green = ship, red = stop.

---

## Phase 1 — Shrink the Surface

Cut live failure paths that aren't on the core journey. Feature-flagged/commented, not deleted — easy to restore.

| Feature | Action | Files |
|---------|--------|-------|
| Payments / Razorpay | Comment out routes + hide UI | `payments.py`, `PurchaseCreditsModal.tsx` |
| Diarization | Disable router + UI | `diarization.py` (679 lines), `diarization/` components |
| Share Notes | Hide from UI, keep backend dormant | `ShareNotesDialog.tsx`, `sharing.py` |
| Calendar reminders | Disable (broken redirect + auth loss) | `calendar.py`, `CalendarIntegrationSettings.tsx` |
| E2EE encryption | Disable (untested key management) | `EncryptionSettings.tsx`, `encryption_service.py` |

**Deliverable:** Running app exposes only the core journey + Recall + credits + admin.

---

## Phase 2 — Transcription Quality Spike *(run in parallel)*

Quality is non-negotiable. Notes and AI insights are only as good as the transcript. Standardize on one provider with real numbers.

**Candidates to evaluate on real Hinglish audio:**
1. **Groq Whisper Large v3** — current streaming default, cheaper, was "not satisfying"
2. **ElevenLabs** — current prod path, had repeated 401/exhausted-key errors  
3. **Google Cloud Speech-to-Text v2 (Chirp)** — user's preferred investigation target
4. **Web Speech API (browser-native)** — see separate section below

**Eval method:**
- Feed 5–10 real meeting clips (Hinglish, 30s–2min each) through each provider
- Measure: Word Error Rate (WER) vs hand-corrected reference, latency, cost/hour
- Decide once, wire it everywhere, collapse the 3-provider branch in `audio.py`

**Offline path (v2 research track, not v1):**
- `whisper.cpp` + `ParakeetModelManager.tsx` already scaffolded in the codebase
- Viable for privacy/no-internet use cases — but offline = on-device = latency hit
- Document the path now, build in v2

**Deliverable:** Short decision memo with numbers + one provider wired everywhere in v1.

---

## Phase 3 — Harden the 4 Core Features

### 3.1 Record + Live Transcript

| Failure Mode | Fix |
|---|---|
| WebSocket drops mid-meeting | `session_reconciler.py` + `STREAMING_RESUME_GRACE_SECONDS` — verify it works |
| nginx doesn't proxy `/ws/` in prod | Audit says it doesn't. Fix `nginx.conf` (already has the change — verify deployed) |
| Audio dropped on reconnect | Test reconnect path (`tests/integration/test_reconnection.py`) |
| Rolling-buffer dedup producing garbage | Unit test `_remove_overlap` with real boundary cases |

### 3.2 Notes / Summary Generation

| Failure Mode | Fix |
|---|---|
| Stuck "generating" spinner | Hard timeout + terminal error state in `summary_processes` table |
| Celery task silently dies | Add Sentry breadcrumb + dead-letter logging to `tasks/generate_notes.py` |
| LLM returns garbage on short transcript | Guard: minimum transcript length before triggering |

### 3.3 AI Chat / Catch-Up

| Failure Mode | Fix |
|---|---|
| RAG retrieves wrong context | Verify vector embeddings are actually indexed after meeting ends |
| Thin transcript → bad answer | Graceful "not enough transcript yet" response |
| Catch-up during meeting | Test the "last N minutes" filter in `chat.py` |

### 3.4 Download Recording

The download path is **confirmed broken** in the audit. The fix chain:

1. `STORAGE_TYPE` aligned to GCP → `generate_signed_url()` returns real GCP signed URL
2. For local fallback: `/audio/signed/{token}` endpoint exists in `main.py` ✅ — verify it actually serves the file
3. nginx `/audio/` proxy wired ← verify deployed
4. E2E test: complete a meeting → click Download → file arrives

---

## Phase 4 — Fix the Two Important-But-Broken Pillars

### 4.1 Recall.ai (Your #1 keeper)

Current state: **completely broken in prod**. Webhook URL is an expired Pinggy free tunnel. Zero Recall config in `.env.prod`.

Fix sequence:
- [ ] Set `RECALL_WEBHOOK_URL=https://meet.quexio.com/api/bot/webhook` in `.env.prod`
- [ ] Add `RECALL_API_KEY` and `RECALL_WEBHOOK_SECRET` to `.env.prod`
- [ ] Test full loop: invite bot → bot joins Zoom/Teams/Meet → audio → transcript → notes → credits decrement
- [ ] Add `bot.py` routes to the integration test suite

### 4.2 Production Analytics

Current state: analytics not arriving in prod. Root-cause unknown.

Investigation targets:
- `AnalyticsProvider.tsx` — is the consent flag blocking in prod?
- `analytics.py` router — CORS or auth issue in prod?
- Any client-side env var (`NEXT_PUBLIC_`) missing from Vercel?

Fix: identify the break, add a `/health/deep` check for analytics pipeline, confirm prod data flows.

---

## Phase 5 — Internal Credit System (Admin Grant Path)

Payments are cut. Credits stay as the usage gate.

**The requirement:** You tell your agent "reset `example@appointy.com` usage limit" and it works.

**Build:**
- [ ] `backend/scripts/grant_credits.py --email X --amount N` — idempotent, writes `credit_ledger` entry
- [ ] Confirm Celery Beat weekly reset fires in prod (`docker-compose.prod.yml` — already added per audit)
- [ ] Simple admin endpoint (`POST /admin/credits/grant`) guarded by admin role check (RBAC in `core/rbac.py`)

---

## Phase 6 — Lock It Down

### Tests (already have a real suite — wire it)

| Suite | Coverage |
|---|---|
| `test_ws_streaming.py` | WebSocket connect → audio → transcript → disconnect |
| `test_audio_reliability.py` | Drop/reconnect/backpressure |
| `test_reconnection.py` | Resume within grace period |
| `test_notes_and_chat.py` | Notes generation + RAG chat |
| `test_storage.py` | Recording URL → download |
| `test_celery_tasks.py` | Credit reset fires correctly |
| Playwright smoke | Login → record → transcript → notes → download (1 happy-path E2E) |

**Gate:** All suites run on every PR push. Merge is blocked on green.

### Monitoring

- [ ] Set `SENTRY_DSN` in prod (backend code is already wired)
- [ ] Set `NEXT_PUBLIC_SENTRY_DSN` in Vercel env vars
- [ ] Point `/health/deep` at uptime monitoring (e.g. UptimeRobot or Grafana Cloud free tier)
- [ ] Alert on: dead transcription key (401), Celery worker unreachable, DB connection failure

---

## Security Flag (Do Before Any External Users)

> A GCP service-account key was committed in git history at `3af770e`. Rotate it now even for internal use:
> 1. Delete the leaked key in GCP Console → create a new one
> 2. Purge from git history: `git filter-repo --path backend/gcp-service-account.json --invert-paths`

---

## Sequencing

```
Week 1:  Phase 0 (config) + Phase 1 (shrink) + Phase 2 begins (eval audio)
Week 2:  Phase 3 (harden 4 core features) + Phase 4.1 (Recall.ai)
Week 3:  Phase 4.2 (analytics) + Phase 5 (credit admin) + Phase 6 (tests + monitoring)
```

---

## What I Need From You

1. **5–10 real Hinglish meeting audio clips** (any length, WAR format preferred) for the Phase 2 transcription eval
2. **Confirm**: GCP billing enabled for Speech-to-Text API? (So I can include Google Chirp in the eval)
3. **Recall.ai API key** for Phase 4.1 (or confirm the key exists in your secure store)
4. **Sentry org slug** or a fresh DSN for backend + frontend

---

## Open Decisions

| Decision | Status |
|---|---|
| Transcription provider | ⏳ Phase 2 eval — see transcription section |
| Google Web Speech API viability | ⏳ See discussion below |
| Calendar reminders: defer or cut? | ⏳ Your call — currently treating as defer |
| E2EE: defer or cut? | ⏳ Your call — currently treating as defer |
