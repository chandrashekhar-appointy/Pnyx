# Comprehensive Audit

Date: 2026-03-19
Scope: backend, frontend, docs, agent files, configs, scripts, tests, CI
Mode: audit-only, no product code changes
Severity lens: internal-only deployment first; external-readiness blockers are still called out separately

## Executive Summary

The repository is functional, but it is not internally coherent. The biggest problems are not isolated bugs; they are trust-boundary failures and documentation drift:

- Security-critical secrets are committed in source and container configs.
- Authentication is weakened by an explicit JWT audience bypass.
- An admin reindex endpoint is publicly callable.
- WebSocket auth is transported in the query string.
- Several shipped flows are broken because frontend and backend contracts no longer match.
- The docs and agent guides materially misdescribe the system, including the database, auth guarantees, routing, platform state, and feature completion.

The codebase looks like a live product plus several partially-abandoned migration paths. The audit found multiple places where AI-generated or provisional logic was never normalized into production-quality code.

## Method

Work performed:

- Repo-wide static scan with `rg` for secrets, auth seams, admin paths, legacy code, TODO/debug markers, and contract mismatches.
- Route inventory from FastAPI decorators and router prefixes.
- Auth-boundary review across `deps.py`, `security.py`, NextAuth, middleware, sharing, analytics, calendar, and WebSocket entrypoints.
- Product/documentation review across `README.md`, `AGENTS.md`, `CLAUDE.md`, `frontend/README.md`, `docs/`, and `pnyx-docs/`.
- Focused runtime validation after bootstrapping missing dependencies.

Validation commands and results:

- `cd backend && . .venv_py311/bin/activate && python -m pytest tests/unit/test_elevenlabs_client.py -q` -> `11 passed`
- `cd backend && . .venv_py311/bin/activate && python -m pytest tests/integration/test_health.py -q` -> `1 passed`
- `cd backend && . .venv_py311/bin/activate && python -m pytest tests/integration/test_chat_and_catchup.py -q` -> `3 passed`
- `cd backend && . .venv_py311/bin/activate && python -m pytest --collect-only -q` -> collection fails because `tests/unit/test_ai_host_participant.py` imports missing `HostEventType`
- `cd frontend && pnpm build` -> production build succeeds
- `cd frontend && pnpm exec playwright test --list` -> only `tests/e2e/smoke.spec.ts`
- `cd frontend && pnpm lint` -> interactive ESLint setup prompt instead of a deterministic lint run

Bootstrap findings:

- `python3` is `3.13.5`, but backend setup required `python3.11` to install the pinned stack successfully.
- `frontend` dependencies were not installed initially.

## Actual System Inventory

### Runtime reality

- Frontend: Next.js 14 web app with NextAuth, browser audio capture, and a single WebSocket audio stream client.
- Backend: FastAPI app with HTTP routes, one WebSocket endpoint, startup schedulers, and optional Celery/Redis audio-finalization integration.
- Persistence: PostgreSQL via `asyncpg`, not SQLite. Several docs still claim SQLite.
- Background jobs:
  - `CalendarReminderScheduler` starts on app startup in [`backend/app/main.py:87`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/main.py#L87)
  - `AudioSessionReconciler` starts on app startup in [`backend/app/main.py:103`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/main.py#L103)
  - Optional Celery enqueue path exists in audio finalization logic in [`backend/app/api/routers/audio.py:697`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/audio.py#L697)

### Public surface and auth boundaries

| Surface | Paths | Auth boundary | Notes |
|---|---|---|---|
| Health | `/health` | Public | Simple health probe |
| Meetings | `/get-meetings`, `/get-meeting/{id}`, `/save-meeting-title`, `/delete-meeting`, `/list-meetings`, AI host skill routes | Auth required | Uses `Depends(get_current_user)` |
| Transcripts | `/process-transcript`, `/save-summary`, `/save-transcript`, `/get-summary/{id}`, notes/version routes | Auth required | Uses `Depends(get_current_user)` |
| Chat | `/chat-meeting`, `/catch-up` | Auth required | Uses `Depends(get_current_user)` |
| Search | `/search-context` | Public | Returns empty results; no auth dependency |
| Audio WS | `/ws/streaming-audio` | Query-string token | Token passed as `auth_token` query parameter |
| Audio upload/reports | upload and recording/session report routes | Auth required | Some routes additionally gate on hardcoded admin email |
| Calendar | `/api/calendar/*` | Mostly auth required | `/google/callback` is public OAuth callback |
| Sharing | `/api/sharing/*` | Auth required except `/view/{share_token}` | Public token route just redirects to frontend |
| Analytics | `/analytics/track` | Public | Accepts client-provided identity data |
| Analytics admin | `/analytics/dashboard/metrics` | Auth required, hardcoded single admin email | Contains SQL injection sink |
| Feedback | `/feedback/*` | Auth required | Admin status handled separately |
| Admin | `/admin/reindex-all` | Public | No auth dependency |

## Reality Check Matrix

| Claim | Source | Status | Evidence |
|---|---|---|---|
| Backend uses SQLite/aiosqlite | `README.md`, `AGENTS.md`, `CLAUDE.md` | Contradicted | Runtime DB layer uses `asyncpg` and requires `DATABASE_URL` in [`backend/app/db/manager.py:27-35`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/db/manager.py#L27) |
| Multi-participant collaborative web product is implemented | `README.md`, `AGENTS.md`, `CLAUDE.md`, `pnyx-docs/README.md` | Contradicted | No room/session router model exists; sharing is still per-user after recording, and the main audio path is single authenticated client over one WS |
| Tauri is removed / pure web only | `AGENTS.md`, `CLAUDE.md` | Partial | No Tauri app is active, but `frontend/package.json` still declares `electron/main.js` in [`frontend/package.json:5`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/package.json#L5), and legacy docs remain |
| JWT audience check is enforced | `pnyx-docs/features/AUTH_AND_RBAC.md` | Contradicted | Audience verification is explicitly disabled in [`backend/app/core/security.py:69-88`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/core/security.py#L69) |
| Sidebar search uses `/search-transcripts` and still works after vector disablement | `pnyx-docs/features/VECTOR_DB_DISABLEMENT.md` | Contradicted | Frontend calls `/search-transcripts`, but backend exposes only `/search-context`, which returns an empty list |
| Frontend/backend local ports are `3118` and `5167` everywhere | repo docs | Partial | Several docs and OAuth defaults still point to `3000` or `8000` |
| Phase 7 is completed | `AGENTS.md`, `CLAUDE.md` | Partial / unverifiable | Search endpoint is stubbed and docs disagree with `pnyx-docs/README.md`, which still marks Phase 7 in progress |

## Findings

### Critical

#### C1. Production database credentials are committed in source and container configs

- Evidence:
  - [`backend/docker-compose.yml:40`](/Users/vibhutripathi/Documents/Code/pnyx/backend/docker-compose.yml#L40)
  - [`backend/Dockerfile.app:38`](/Users/vibhutripathi/Documents/Code/pnyx/backend/Dockerfile.app#L38)
  - [`backend/app/vector_store.py:66-69`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/vector_store.py#L66)
  - [`backend/cleanup_legacy_tables.py:4`](/Users/vibhutripathi/Documents/Code/pnyx/backend/cleanup_legacy_tables.py#L4)
  - [`backend/setup_vector_table.py:5`](/Users/vibhutripathi/Documents/Code/pnyx/backend/setup_vector_table.py#L5)
- Impact:
  - Any repository reader or image consumer can recover the production Neon DSN.
  - The secret is duplicated across codepaths, increasing rotation blast radius.
- Exploit path:
  - Pull repo or inspect built image -> extract DSN -> connect to production database.
- Confidence: High
- Recommended fix:
  - Rotate the exposed Neon credential immediately.
  - Remove all inline DSNs and fail hard if `DATABASE_URL` is absent.
  - Add secret scanning in CI and pre-commit.
- Validation:
  - `rg -n "postgresql://.*neon" backend -S` should return zero matches after remediation.

#### C2. Google JWT audience verification is intentionally disabled

- Evidence:
  - [`backend/app/core/security.py:69-88`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/core/security.py#L69)
- Impact:
  - Any Google-signed token with a valid signature can be accepted even if it was minted for a different client.
  - This weakens every HTTP and WebSocket route that depends on `verify_google_token`.
- Exploit path:
  - Acquire a valid Google ID token for another OAuth client -> send to backend -> backend accepts signature and bypasses audience enforcement.
- Confidence: High
- Recommended fix:
  - Restore strict `audience=GOOGLE_CLIENT_ID`.
  - Reject mismatches instead of logging and continuing.
  - Add regression tests for wrong-audience tokens.
- Validation:
  - Negative tests with mismatched `aud` must return `401`.

#### C3. `/admin/reindex-all` is publicly callable

- Evidence:
  - [`backend/app/api/routers/admin.py:15-17`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/admin.py#L15)
  - Router has no `Depends(get_current_user)` or admin check.
- Impact:
  - Anyone who can reach the API can trigger a full meeting reindex.
  - This is a resource-exhaustion and data-processing control failure.
- Exploit path:
  - Call `POST /admin/reindex-all` directly without credentials.
- Confidence: High
- Recommended fix:
  - Require authenticated admin authorization.
  - Move long-running reindex behind a job queue with audit logging and rate limiting.
- Validation:
  - Anonymous requests should return `401` or `403`.

#### C4. WebSocket auth token is sent in the URL query string

- Evidence:
  - Client appends `auth_token` in [`frontend/src/lib/audio-streaming/AudioStreamClient.ts:237-246`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/lib/audio-streaming/AudioStreamClient.ts#L237)
  - Server accepts `auth_token` query parameter in [`backend/app/api/routers/audio.py:738-750`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/audio.py#L738)
- Impact:
  - Tokens are more likely to leak into browser history, reverse-proxy logs, error telemetry, and copied URLs.
  - This compounds the weakened JWT validation issue.
- Exploit path:
  - Any infrastructure or debugging layer that logs URLs can capture bearer-equivalent auth material.
- Confidence: High
- Recommended fix:
  - Move auth to headers during the HTTP upgrade or to a short-lived signed WS ticket.
  - Scrub URL logging until migration is complete.
- Validation:
  - No auth-bearing query parameter should be present in WS connection URLs.

### High

#### H1. Analytics admin endpoint contains a SQL injection sink and the public ingest endpoint accepts forged identity data

- Evidence:
  - Public tracking route in [`backend/app/api/routers/analytics.py:30-65`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/analytics.py#L30)
  - Raw string interpolation in [`backend/app/api/routers/analytics.py:85-120`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/analytics.py#L85)
- Impact:
  - The admin metrics route interpolates `user_filter` directly into SQL.
  - The track endpoint effectively trusts frontend-supplied `user_id` and does not correctly validate the bearer token.
- Exploit path:
  - Authenticated admin sends crafted `user_filter` -> injected SQL executes.
  - Any caller posts arbitrary `user_id` to poison analytics.
- Confidence: High
- Recommended fix:
  - Parameterize all SQL.
  - Make `/analytics/track` either explicitly anonymous with server-side generated identity or authenticated with a correct dependency.
  - Separate ingestion identity from display identity.
- Validation:
  - Fuzz `user_filter` with quote payloads and confirm they are treated as data, not SQL.

#### H2. Encryption fallback silently converts stored secrets into empty strings

- Evidence:
  - [`backend/app/core/encryption.py:7-37`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/core/encryption.py#L7)
  - Calendar token writes rely on `encrypt_key` in [`backend/app/db/manager.py:300-390`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/db/manager.py#L300)
- Impact:
  - If `MASTER_KEY` is absent or invalid, secret writes succeed with empty ciphertext and reads silently return `""`.
  - This is both a security and data-loss failure.
- Exploit/regression path:
  - Misconfigure `MASTER_KEY` -> save BYOK or calendar tokens -> values are blanked without an explicit error.
- Confidence: High
- Recommended fix:
  - Fail startup if encryption is required and `MASTER_KEY` is invalid.
  - Make encrypt/decrypt raise explicit errors.
  - Add migration/health checks for blank encrypted fields.
- Validation:
  - Saving a token without a valid `MASTER_KEY` must fail loudly.

#### H3. Sharing flow is internally contradictory and likely broken for email recipients

- Evidence:
  - AI-generated unresolved comments and first-token reuse in [`backend/app/api/routers/transcripts.py:1145-1159`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/transcripts.py#L1145)
  - Public token route only redirects in [`backend/app/api/routers/sharing.py:116-127`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/sharing.py#L116)
  - Frontend shared view ignores the `token` query param and fetches by authenticated user + meeting id in [`frontend/src/app/meeting-details/page.tsx:102-145`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/app/meeting-details/page.tsx#L102)
- Impact:
  - Email link semantics are undefined.
  - The backend creates per-recipient tokens, then emails a single URL derived from the first token.
  - The frontend does not redeem the token anyway.
- Regression path:
  - Recipient opens emailed link -> frontend ignores token -> access depends on separate authenticated share record, not the emailed token.
- Confidence: High
- Recommended fix:
  - Decide between authenticated share records and bearer-style share tokens; do not mix both.
  - If tokens are used, frontend must redeem them and backend must authorize by token, not by current user email alone.
  - Remove the unresolved comment block after implementing one clear model.
- Validation:
  - End-to-end test with two recipients should prove each recipient can access only their intended share path.

#### H4. Frontend and backend API contracts have drifted enough to break shipped UX

- Evidence:
  - Frontend sidebar calls `/search-transcripts` in [`frontend/src/components/Sidebar/SidebarProvider.tsx:205-210`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/components/Sidebar/SidebarProvider.tsx#L205)
  - Backend only exposes `/search-context` and currently returns `[]` in [`backend/app/api/routers/chat.py:376-390`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/chat.py#L376)
  - Frontend calendar connect calls `/api/calendar/connect?request_write_scope=false` in [`frontend/src/components/CalendarConnectPrompt.tsx:80-88`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/components/CalendarConnectPrompt.tsx#L80)
  - Backend exposes `/api/calendar/google/connect` in [`backend/app/api/routers/calendar.py:79-88`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/calendar.py#L79)
- Impact:
  - Search and calendar-connect flows can fail even when the UI suggests they exist.
- Confidence: High
- Recommended fix:
  - Generate or share typed API contracts.
  - Add route-level integration tests for every frontend API callsite.
  - Remove or feature-flag incomplete UI affordances.
- Validation:
  - Frontend call graph should map one-to-one to existing backend routes in CI.

#### H5. Auth and RBAC docs materially overstate what the code enforces

- Evidence:
  - Docs claim audience enforcement in [`pnyx-docs/features/AUTH_AND_RBAC.md:74-79`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/features/AUTH_AND_RBAC.md#L74)
  - Actual audience bypass in [`backend/app/core/security.py:69-88`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/core/security.py#L69)
  - RBAC spec describes workspace admins and meeting roles in [`pnyx-docs/features/RBAC_SPEC.md:28-70`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/features/RBAC_SPEC.md#L28)
  - Implementation only checks `owner_id` and optional `meeting_permissions` in [`backend/app/core/rbac.py:18-65`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/core/rbac.py#L18)
- Impact:
  - Operators and future contributors are likely to assume protections that do not exist.
  - This is dangerous because the docs are written as if the system is already hardened.
- Confidence: High
- Recommended fix:
  - Rewrite docs to describe current behavior, not intended behavior.
  - Keep future-state design docs separate from implementation docs.
- Validation:
  - Every security doc claim should be traceable to a current code path and test.

#### H6. Hardcoded organization-specific policy is spread across client and server

- Evidence:
  - Frontend domain restriction in [`frontend/src/lib/auth.ts:48-80`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/lib/auth.ts#L48)
  - Backend domain restriction and “temporary bypass” comment in [`backend/app/api/deps.py:52-58`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/deps.py#L52)
  - WebSocket domain restriction in [`backend/app/api/routers/audio.py:676-684`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/audio.py#L676)
  - Hardcoded single admin email in [`backend/app/api/routers/analytics.py:74-75`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/analytics.py#L74), [`frontend/src/app/dashboard/page.tsx:29-34`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/app/dashboard/page.tsx#L29), and [`frontend/src/components/Sidebar/index.tsx:43-45`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/components/Sidebar/index.tsx#L43)
- Impact:
  - The product is effectively hard-wired to one company and one admin identity.
  - This blocks reuse, makes staging fragile, and undermines any claim of configurable RBAC.
- Confidence: High
- Recommended fix:
  - Move allowed domains and admin roles to environment/config and persist roles in the database.
  - Remove hardcoded identities from the frontend entirely.
- Validation:
  - A staging org should be configurable without code changes.

#### H7. Database access creates a new connection per request and explicitly defers pooling

- Evidence:
  - [`backend/app/db/manager.py:42-76`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/db/manager.py#L42)
- Impact:
  - Avoidable connection churn, higher latency, and worse failure behavior under moderate concurrency.
  - This is especially risky with WebSocket-heavy traffic and background jobs.
- Confidence: High
- Recommended fix:
  - Create a global asyncpg pool on startup and inject it.
  - Reuse transactions and shared connection settings through the app lifetime.
- Validation:
  - Load tests should show stable connection counts and lower p95 latency.

#### H8. CI uses an unpinned third-party review action from `main`

- Evidence:
  - [`.github/workflows/ai-review.yml:21-24`](/Users/vibhutripathi/Documents/Code/pnyx/.github/workflows/ai-review.yml#L21)
- Impact:
  - A mutable upstream branch controls CI behavior in a privileged GitHub context.
- Confidence: High
- Recommended fix:
  - Pin the action to a commit SHA or vendor it.
  - Review token permissions and minimize them if the workflow remains.
- Validation:
  - CI should only reference immutable action SHAs.

### Medium

#### M1. Main app metadata, docs, and runtime ports are inconsistent

- Evidence:
  - App title/version/port in [`backend/app/main.py:50-53`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/main.py#L50) and [`backend/app/main.py:129-132`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/main.py#L129)
  - Docs and configs reference `3118`, `5167`, `3000`, and `8000` across the repo.
- Impact:
  - Confusing operator setup, misleading API docs, and brittle local deployment behavior.
- Confidence: High
- Recommended fix:
  - Define one canonical local/dev/prod port matrix and generate docs from it where possible.

#### M2. Calendar OAuth settings flow still defaults to the wrong frontend origin

- Evidence:
  - [`backend/app/services/calendar/google_oauth.py:37-38`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/services/calendar/google_oauth.py#L37)
- Impact:
  - Error redirects and completion flows can bounce users to `localhost:3000/settings`, which is stale for this repo.
- Confidence: High
- Recommended fix:
  - Remove hardcoded fallback or align it with the actual frontend config.

#### M3. The analytics product surface is partly stubbed, but the UI presents it as a real feature

- Evidence:
  - Numerous no-op methods in [`frontend/src/lib/analytics.ts:90-186`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/lib/analytics.ts#L90)
  - Provider still initializes and identifies users in [`frontend/src/components/AnalyticsProvider.tsx:46-125`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/components/AnalyticsProvider.tsx#L46)
- Impact:
  - Product telemetry semantics are unreliable.
  - Dashboard numbers are easy to misinterpret as real usage truth.
- Confidence: Medium
- Recommended fix:
  - Either finish the analytics model or mark it experimental and remove the dashboard from normal flows.

#### M4. Dead or legacy frontend scaffolding remains in active paths

- Evidence:
  - Electron entry in [`frontend/package.json:5`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/package.json#L5)
  - Empty config file [`frontend/src/config/api.ts`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/config/api.ts) (`0` lines)
  - Deprecated local-inference stub in [`frontend/src/lib/parakeet.ts:1-29`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/src/lib/parakeet.ts#L1)
  - Committed Windows installer binary `frontend/vs_buildtools.exe`
- Impact:
  - Raises maintenance cost and muddies the migration state.
  - Increases the chance of future contributors reviving the wrong codepath.
- Confidence: High
- Recommended fix:
  - Delete dead files and binaries.
  - Keep migration artifacts in docs or archived branches, not the runtime tree.

#### M5. Test suite health is overstated; full collection is currently broken

- Evidence:
  - `tests/unit/test_ai_host_participant.py` imports `HostEventType` in [`backend/tests/unit/test_ai_host_participant.py:4`](/Users/vibhutripathi/Documents/Code/pnyx/backend/tests/unit/test_ai_host_participant.py#L4)
  - `HostEventType` is absent from [`backend/app/schemas/ai_participant.py:1-85`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/schemas/ai_participant.py#L1)
  - Playwright config exists, but only one smoke test is present in [`frontend/playwright.config.ts:3-20`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/playwright.config.ts#L3)
- Impact:
  - The repository has some passing targeted tests, but not a trustworthy regression net.
- Confidence: High
- Recommended fix:
  - Make `pytest --collect-only` mandatory in CI.
  - Add contract tests for auth, sharing, search, and calendar flows.
  - Expand frontend e2e beyond one root-page smoke test.

#### M6. Backend dependency pins are not ready for the repo’s default Python interpreter

- Evidence:
  - Pinned stack includes `asyncpg==0.29.0` and `psycopg2-binary==2.9.9` in [`backend/requirements.txt:30-31`](/Users/vibhutripathi/Documents/Code/pnyx/backend/requirements.txt#L30)
  - Local default interpreter is `Python 3.13.5`, while successful bootstrap required `Python 3.11.13`
- Impact:
  - Fresh setup fails on a modern local Python unless the operator already knows to downshift versions.
- Confidence: High
- Recommended fix:
  - Pin supported Python versions explicitly in docs and tooling.
  - Add a `.python-version`, `pyproject.toml`, or Docker-only dev path.

#### M7. Linting is not in a clean CI-ready state

- Evidence:
  - Script uses `next lint` in [`frontend/package.json:11`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/package.json#L11)
  - Repo uses flat config in [`frontend/eslint.config.mjs:1-16`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/eslint.config.mjs#L1)
  - `pnpm lint` triggered interactive setup instead of a deterministic run during validation
- Impact:
  - Static analysis can silently disappear from developer workflows and CI.
- Confidence: Medium
- Recommended fix:
  - Move to a direct `eslint .` command compatible with the checked-in config and fail CI on lint errors.

#### M8. Search product is presented as implemented while backend returns empty results

- Evidence:
  - Stubbed search in [`backend/app/api/routers/chat.py:382-389`](/Users/vibhutripathi/Documents/Code/pnyx/backend/app/api/routers/chat.py#L382)
  - Docs still describe working search in `pnyx-docs/features/VECTOR_DB_DISABLEMENT.md`
- Impact:
  - Users and developers will misread the system’s retrieval capabilities.
- Confidence: High
- Recommended fix:
  - Either restore search or remove the feature from UI/docs until it is truly live.

### Low

#### L1. Product naming is inconsistent across the repo

- Evidence:
  - `Pnyx`, `Meeting Co-Pilot`, `Meetily`, `Meeting Summarizer API`, and `meet.quexio.com` all appear in active code and docs.
- Impact:
  - Branding and operator confusion; low direct technical risk.
- Recommended fix:
  - Choose one canonical product name and codify it in app metadata, docs, env vars, and URLs.

#### L2. Several documentation sets should be archived or reclassified

- Unsafe or misleading:
  - [`AGENTS.md`](/Users/vibhutripathi/Documents/Code/pnyx/AGENTS.md)
  - [`CLAUDE.md`](/Users/vibhutripathi/Documents/Code/pnyx/CLAUDE.md)
  - [`frontend/README.md`](/Users/vibhutripathi/Documents/Code/pnyx/frontend/README.md)
  - [`docs/BUILDING.md`](/Users/vibhutripathi/Documents/Code/pnyx/docs/BUILDING.md)
  - [`pnyx-docs/features/AUTH_AND_RBAC.md`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/features/AUTH_AND_RBAC.md)
  - [`pnyx-docs/features/VECTOR_DB_DISABLEMENT.md`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/features/VECTOR_DB_DISABLEMENT.md)
- Partially stale:
  - [`README.md`](/Users/vibhutripathi/Documents/Code/pnyx/README.md)
  - [`pnyx-docs/README.md`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/README.md)
  - [`pnyx-docs/features/RBAC_SPEC.md`](/Users/vibhutripathi/Documents/Code/pnyx/pnyx-docs/features/RBAC_SPEC.md)
- Recommended fix:
  - Split docs into `current-state`, `design-proposals`, and `archived`.

#### L3. E2E coverage is effectively a single smoke check

- Evidence:
  - Only one listed test in `tests/e2e/smoke.spec.ts`
- Impact:
  - Low confidence in user-facing flows, especially auth, recording, sharing, and calendar.
- Recommended fix:
  - Add minimal smoke coverage for login, record start/stop, meeting details, sharing, and dashboard gating.

## Remediation Plan

### Quick wins

1. Rotate the exposed Neon credential and purge all committed DSNs.
2. Re-enable JWT audience verification.
3. Lock down `/admin/reindex-all`.
4. Replace query-string WebSocket auth with header or short-lived ticket auth.
5. Fix the two confirmed route mismatches: search and calendar connect.
6. Correct OAuth/frontend URL defaults (`3118` and `5167` vs `3000` and `8000`).
7. Delete dead runtime scaffolding: `electron/main.js` entry, `frontend/vs_buildtools.exe`, empty config file, deprecated stubs.

### Targeted fixes

1. Replace analytics SQL string interpolation with parameterized queries.
2. Make encryption startup fail fast when `MASTER_KEY` is missing or invalid.
3. Pick one sharing model and remove the mixed authenticated-share-plus-token design.
4. Introduce a real DB pool on startup and pass it through dependencies/services.
5. Convert security and auth docs into current-state documents with tests referenced.
6. Make lint and full test collection mandatory in CI.

### Architectural refactors

1. Introduce a typed API contract layer between frontend and backend.
2. Separate “implemented behavior” docs from “planned architecture” docs.
3. Replace hardcoded org rules with configurable policy and stored roles.
4. Consolidate product identity, deployment config, and environment handling into a single source of truth.
5. Remove or feature-flag incomplete subsystems instead of leaving them half-visible in production UI.

## Documentation Audit Summary

| Asset | Classification | Why |
|---|---|---|
| `README.md` | Partially stale | Web app description is roughly right, but DB, collaboration, and local setup details are not reliable |
| `AGENTS.md` | Unsafe/misleading | Describes outdated phases, docs paths, database, and feature completion as current truth |
| `CLAUDE.md` | Unsafe/misleading | Same drift pattern as `AGENTS.md` |
| `frontend/README.md` | Unsafe/misleading | Old repo origin, wrong backend port, malformed localhost text, local-only/privacy claims |
| `docs/BUILDING.md` | Unsafe/misleading | Tauri/Meetily/Linux GPU build guide for a different product state |
| `pnyx-docs/README.md` | Partially stale | Better organized, but phase status and product reality still drift |
| `pnyx-docs/features/AUTH_AND_RBAC.md` | Unsafe/misleading | Claims audience verification and up-to-date auth architecture that the code does not enforce |
| `pnyx-docs/features/RBAC_SPEC.md` | Partially stale / aspirational | Reads like future-state design, not current implementation |
| `pnyx-docs/features/VECTOR_DB_DISABLEMENT.md` | Unsafe/misleading | Claims an endpoint exists and works when it does not |

## Validation Gaps

- No live production/cloud audit was performed.
- I did not execute destructive or mutation-heavy flows against live data.
- Frontend e2e validation is limited because the suite only contains one smoke test.
- Some subsystems are partially implemented enough that behavior must be clarified before they can be meaningfully hardened.

## Bottom Line

The repo is salvageable, but it needs a trust reset:

- Security assumptions must be re-established first.
- Contracts between frontend, backend, and docs need to be made explicit.
- Migration leftovers and AI-generated provisional logic need to be either completed or removed.

Until the critical items are addressed, this codebase should not be treated as secure, portable, or accurately documented.
