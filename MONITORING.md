# Pnyx — Monitoring & Alerting Setup

## Status

| Component | Code wired? | Needs |
|---|---|---|
| Backend Sentry | ✅ `main.py` | Set `SENTRY_DSN` in `.env.prod` |
| Frontend Sentry | ✅ `sentry.*.config.ts` | Set `NEXT_PUBLIC_SENTRY_DSN` in Vercel |
| `/health/deep` | ✅ Running | Point uptime monitor at it |
| Analytics dashboard | ✅ `/dashboard` route | `analytics_events` table now live |

---

## 1. Sentry (error tracking)

### Backend
In `backend/.env.prod`, uncomment and fill:
```
SENTRY_DSN=https://xxx@oXXX.ingest.sentry.io/XXXX
SENTRY_TRACES_SAMPLE_RATE=0.05
RELEASE_VERSION=<git SHA or tag>
```
Get the DSN from: Sentry Dashboard → Project Settings → Client Keys.

### Frontend (Vercel)
In Vercel → Project → Settings → Environment Variables, add:
```
NEXT_PUBLIC_SENTRY_DSN=https://xxx@oXXX.ingest.sentry.io/XXXX
NEXT_PUBLIC_SENTRY_ENVIRONMENT=production
NEXT_PUBLIC_SENTRY_RELEASE=<same git SHA>
NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE=0.05
```
Redeploy after adding. Verify in Sentry Issues tab after first error.

---

## 2. `/health/deep` — uptime monitoring

The endpoint at `GET /health/deep` checks every critical dependency (DB,
Redis, GCP bucket, Groq, ElevenLabs) and returns:
```json
{
  "overall": "ok" | "degraded" | "down",
  "components": {
    "database":   { "status": "ok", "latency_ms": 45 },
    "redis":      { "status": "ok" },
    "gcp_bucket": { "status": "ok" },
    "groq":       { "status": "ok" },
    "elevenlabs": { "status": "ok", "scribe_v2_available": true }
  }
}
```
`overall=down` → HTTP 503. `overall=ok|degraded` → HTTP 200.

### Set up UptimeRobot (free, 5-min interval)
1. Go to [uptimerobot.com](https://uptimerobot.com) → Add Monitor
2. Type: **HTTP(s)** → Keyword Monitor
3. URL: `https://pnyx-dev-206432.bifrost.saastack.site/health/deep`
4. Keyword (must exist): `"overall": "ok"`  (alert if missing → degraded/down)
5. Interval: 5 minutes
6. Alert contact: your email/Slack webhook

### ElevenLabs key exhaustion alert
The `/health/deep` response includes `elevenlabs.status`. If it becomes
`"auth_failed"` or `"low_credits"`, uptime monitor will alert.
Rotate `ELEVENLABS_API_KEY` in `.env.prod` when this fires.

---

## 3. CI gate (GitHub Actions)

`pre-deploy.yml` runs on every PR and push to `main`:

| Job | What it does | Hard gate? |
|---|---|---|
| `backend` | ruff lint + migrations + 34 unit tests + security regression | ✅ Yes — blocks merge |
| `frontend` | pnpm lint + Next.js build check | ✅ Yes |
| `env-validation` | `validate_env.py` on `.env.prod` | ✅ Yes if file present |
| `secret-scan` | gitleaks scan | ⚠️ `continue-on-error: true` |

The `secret-scan` continues-on-error to not block on false positives — review
manually if it fires. Promote it to a hard gate once you've tuned the
`.gitleaks.toml` allowlist.

---

## 4. What to do when something breaks in prod

| Symptom | Check |
|---|---|
| Transcription silent | `elevenlabs.status` in `/health/deep` → rotate key if `auth_failed` |
| Bot meetings fail | `recall.ai` API key/region; check Recall dashboard |
| Notes never generate | Celery worker alive? `redis` in `/health/deep` → restart worker |
| DB errors | `database.latency_ms` spike in `/health/deep`; check Neon dashboard |
| Analytics empty | `analytics_events` table exists; `/analytics/track` router registered |
| Recording download fails | GCP bucket permissions; `gcp_bucket` in `/health/deep` |
