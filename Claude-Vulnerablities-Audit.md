# 🚨 Production Readiness Audit — Meeting Co-Pilot

**Verdict: NOT production-ready.** There are **critical**, **high**, and **medium** severity issues across security, testing, observability, and infrastructure.

---

## 🔴 CRITICAL — Must Fix Before Any Production Deployment

### 1. Secrets Committed to Git History

> [!CAUTION]
> **All API keys, database credentials, OAuth secrets, GCP service account private key, and SMTP passwords are committed in plaintext** to the repository in [.env](file:///home/gagansharma/Projects/meeting-co-pilot/backend/.env), [.env.prod](file:///home/gagansharma/Projects/meeting-co-pilot/backend/.env.prod), and [gcp-service-account.json](file:///home/gagansharma/Projects/meeting-co-pilot/backend/gcp-service-account.json).

**Exposed credentials include:**
| Secret | File |
|---|---|
| OpenAI API Key (`sk-proj-...`) | `.env`, `.env.prod` |
| Gemini API Key | `.env`, `.env.prod` |
| Anthropic API Key | `.env`, `.env.prod` |
| Groq API Key | `.env`, `.env.prod` |
| ElevenLabs API Keys (2) | `.env`, `.env.prod` |
| Deepgram API Key | `.env`, `.env.prod` |
| Tavily API Key | `.env`, `.env.prod` |
| SerpAPI Key | `.env`, `.env.prod` |
| Google OAuth Client Secret | `.env`, `.env.prod`, `frontend/.env.local` |
| Neon Postgres DB credentials | `.env`, `.env.prod` |
| SMTP (Gmail) App Password | `.env` |
| MASTER_KEY (encryption) | `.env`, `.env.prod` |
| NextAuth Secret | `frontend/.env.local` |
| Recall.ai API Key + Webhook Secret | `.env` |
| Razorpay Keys (set to `null` but still present) | `.env` |
| **GCP Service Account Private Key** | `gcp-service-account.json` (committed!) |

Even though `.gitignore` lists `.env*` and `gcp-service-account.json`, the GCP service account was committed in commit `3af770e`. The `.env` / `.env.prod` are listed in backend's `.gitignore` but the patterns **may not match** due to directory-level `.gitignore` precedence.

**Impact:** Anyone with repo access has full access to your database, cloud storage, AI providers, email, and payment systems.

**Fix:** Rotate ALL keys immediately. Use a secrets manager (GCP Secret Manager, Vault, etc.). Run `git filter-repo` to purge history.

---

### 2. No API Rate Limiting

> [!WARNING]
> There is **zero server-side rate limiting** on any HTTP endpoint.

The codebase has no `slowapi`, no custom rate-limiting middleware, and no per-user/per-IP throttling. The only "rate limiting" found is retry-after logic for *upstream* Groq/ElevenLabs API rate limits — not inbound request throttling.

**Impact:** Vulnerable to DDoS, brute-force attacks, and abuse of expensive AI endpoints (OpenAI, Groq, ElevenLabs) leading to unbounded cloud costs.

---

### 3. XSS Vulnerabilities — `dangerouslySetInnerHTML` Without Sanitization

> [!CAUTION]
> Two components render user-controlled content via `dangerouslySetInnerHTML` **without DOMPurify or any sanitization:**

| File | Line |
|---|---|
| [notes/[id]/page.tsx](file:///home/gagansharma/Projects/meeting-co-pilot/frontend/src/app/notes/%5Bid%5D/page.tsx#L174) | `dangerouslySetInnerHTML={{ __html: note.content.split(...)` |
| [RefineNotesSidebar.tsx](file:///home/gagansharma/Projects/meeting-co-pilot/frontend/src/components/MeetingDetails/RefineNotesSidebar.tsx#L52) | `dangerouslySetInnerHTML={{ __html: line }}` |

If `note.content` or `line` contains user-generated or AI-generated content (which it does — meeting notes), an attacker can inject `<script>` tags or event handlers.

**Impact:** Stored XSS → session hijacking, data theft, account takeover.

---

### 4. SQL Injection Vectors — Dynamic Query Construction

> [!WARNING]
> Multiple files build SQL queries using f-strings with variable interpolation:

| File | Example |
|---|---|
| [analytics.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/api/routers/analytics.py#L104) | `f"SELECT COUNT(*) FROM analytics_events WHERE {base_where}"` |
| [manager.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/db/manager.py#L316) | `f"UPDATE summary_processes SET {', '.join(update_fields)} ..."` |
| [manager.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/db/manager.py#L1124) | `f'SELECT "{column_name}" FROM settings WHERE id = ...'` |
| [manager.py](file:///home/gagansharma/Projects/meeting-co-pilot/backend/app/db/manager.py#L3198) | `f"UPDATE meeting_bots SET {', '.join(fields)} ..."` |

While some use parameterized values (`$1`, `$2`), the **column names and WHERE clauses** are composed dynamically from variables. If any of these originate from user input, this is exploitable.

---

## 🟠 HIGH — Must Fix Before Real Users

### 5. Missing Security Headers

No `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, or `X-XSS-Protection` headers are set on either backend or frontend. The Next.js config has no `headers()` function. The backend has no security middleware.

**Impact:** Clickjacking, MIME-sniffing attacks, failure to enforce HTTPS.

---

### 6. CORS Too Permissive for Production

```python
origins = [
    "http://localhost:3118",
    "http://localhost:3000",
    "https://pnyxx.vercel.app",
    "https://meet.quexio.com",
]
allow_methods=["*"]
allow_headers=["*"]
```

- Localhost origins should not be in production config.
- `allow_methods=["*"]` and `allow_headers=["*"]` is overly broad.
- No environment-based CORS configuration.

---

### 7. ~35 Unprotected API Endpoints

Of ~101 route handler definitions, only ~66 use `Depends(get_current_user)`. The following are **unauthenticated**:

| Endpoint | Risk |
|---|---|
| `/analytics/track` | Anyone can inject fake analytics events |
| `/admin/reindex-all` (has `get_admin_user` ✅) | OK |
| `/ws/streaming-audio` | Has custom auth ✅ |
| Bot webhook endpoints | Intended (webhook), but verify secret |
| Calendar OAuth callback | Intended, but verify `state` param |

> [!IMPORTANT]
> The `/analytics/track` endpoint accepts arbitrary event data with no authentication. The code *tries* to extract user info but explicitly doesn't enforce it (lines 36-45 of analytics.py).

---

### 8. Virtually No Test Coverage

| Layer | Files | Coverage |
|---|---|---|
| Backend Unit | 4 test files | AI participant, credit, ElevenLabs only |
| Backend Integration | 5 test files | Audio flows, health check |
| Frontend Unit | **0 test files** | None |
| Frontend E2E | 1 file (`smoke.spec.ts`) | Smoke test only |

**Missing test coverage:**
- No tests for auth/security flows
- No tests for RBAC
- No tests for chat, meetings, transcripts, calendar, sharing, payments, settings routers
- No frontend component tests
- No API contract tests

---

### 9. No Error Monitoring / APM

- No Sentry, Datadog, New Relic, or any APM integration
- No React Error Boundary in the frontend
- No structured logging (uses `print`-style `logging.basicConfig`)
- No centralized error reporting — errors are only in stdout/container logs

---

### 10. Production Environment Still Has `localhost` URLs

[.env.prod](file:///home/gagansharma/Projects/meeting-co-pilot/backend/.env.prod) and [.env](file:///home/gagansharma/Projects/meeting-co-pilot/backend/.env) contain `localhost` references:
- `CALENDAR_OAUTH_REDIRECT_URI=http://localhost:5167/...` (in `.env`)
- `CALENDAR_EMAIL_START_MEETING_URL=http://localhost:3118/...`
- `NEXTAUTH_URL=http://localhost:3118`
- `.env.prod` has the prod URLs but also has a **double slash**: `https://pnyxx.vercel.app//settings`

---

## 🟡 MEDIUM — Should Fix Before Scaling

### 11. No Database Migration Framework

Migrations are raw `.sql` files and standalone Python scripts in `backend/app/migrations/`. There is:
- No Alembic or similar migration runner
- No migration version tracking table
- No rollback capability
- Duplicate migration number: two `014_*.sql` files
- No automated migration on deploy

---

### 12. Giant Router Files — Maintainability Concern

| File | Lines |
|---|---|
| `audio.py` | **3,415 lines** |
| `transcripts.py` | **2,396 lines** |
| `settings.py` | 764 lines (21KB) |

These monolithic files are extremely difficult to review, test, and maintain. `audio.py` at 3,415 lines is a significant code smell.

---

### 13. No Database Backup/Restore Strategy

Using Neon Postgres (cloud), but:
- No documented backup strategy
- No point-in-time recovery plan
- No data retention policy
- No backup verification process

---

### 14. No Health Check Depth

The `/health` endpoint returns a static `{"status": "ok"}` without checking:
- Database connectivity
- Redis connectivity
- External API availability (Groq, ElevenLabs)
- Celery worker health
- Disk space / memory

---

### 15. Frontend Console Log Pollution

**213 `console.log` statements** across the frontend TypeScript/TSX files. These leak implementation details in the browser console and impact performance.

---

### 16. No CI/CD Pipeline Visible

- No GitHub Actions workflow files found in `.github/`
- No automated testing, linting, or deployment pipeline
- Manual deployment process

---

### 17. React Strict Mode Disabled

```js
reactStrictMode: false, // Disabled for BlockNote compatibility
```

This hides potential bugs and race conditions during development.

---

### 18. `DEBUG` Logging Statements Left in Security Code

```python
# security.py
logger.info("DEBUG AUTH: Refreshed Google public keys cache")
```

Debug-level statements with "DEBUG" prefix in production-critical auth code.

---

## Summary Scorecard

| Category | Score | Status |
|---|---|---|
| **Secrets Management** | 🔴 0/10 | Plaintext in repo |
| **Authentication/Authorization** | 🟠 6/10 | Most routes protected, but gaps exist |
| **Input Validation** | 🟠 5/10 | Some Pydantic schemas, but XSS + SQLi vectors |
| **Security Headers** | 🔴 0/10 | None configured |
| **Rate Limiting** | 🔴 0/10 | None |
| **Testing** | 🔴 2/10 | Minimal coverage |
| **Observability** | 🔴 1/10 | Basic logging only |
| **Infrastructure** | 🟡 5/10 | Docker setup exists, but no CI/CD |
| **Code Quality** | 🟡 5/10 | Functional but monolithic files |
| **Database Operations** | 🟡 4/10 | No migration framework, no backups |

---

## Recommended Priority Order

1. **Rotate ALL secrets immediately** — they are in git history
2. Add DOMPurify to all `dangerouslySetInnerHTML` usages
3. Add rate limiting (slowapi or custom middleware)
4. Add security headers (CSP, HSTS, X-Frame-Options)
5. Parameterize all SQL queries fully
6. Set up Sentry or equivalent error monitoring
7. Add CI/CD with automated testing
8. Implement proper secrets management (GCP Secret Manager)
9. Add comprehensive test suite
10. Split monolithic router files
