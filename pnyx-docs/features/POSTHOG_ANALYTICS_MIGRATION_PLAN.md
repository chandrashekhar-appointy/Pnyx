# PostHog Analytics Migration Plan

## 1. Goal

Replace the current partially custom analytics pipeline with PostHog as the primary product analytics system for feature usage, funnels, retention, and behavioral analysis.

This plan is intentionally scoped to product analytics, not backend observability. Application logs, infra metrics, and audit/security logs remain separate concerns.

---

## 2. Why We Are Changing Direction

The current analytics architecture is functional only in pieces:

- Frontend analytics helpers in `frontend/src/lib/analytics.ts` contain real event calls mixed with many no-op or placeholder methods.
- Backend analytics ingestion in `backend/app/api/routers/analytics.py` stores events, but reporting is narrow and requires continued custom dashboard work.
- Product questions like feature adoption, conversion funnels, retention, and cohort behavior are better served by a purpose-built analytics platform than by more custom SQL and charts.

PostHog is the better fit because it gives us:

- Event capture and schema flexibility.
- Funnels, retention, trends, cohorts, and feature flags.
- Session replay and user journey debugging.
- Faster iteration on product questions without building more analytics infrastructure.

---

## 3. Decision

### Chosen Direction

Adopt PostHog as the source of truth for product analytics.

### What We Will Keep

- Existing backend logs for debugging and incident response.
- Existing backend analytics endpoints only as a temporary bridge during migration, if needed.

### What We Will Stop Growing

- The custom analytics database as the long-term product analytics system.
- The custom `/dashboard` analytics experience as the primary way to answer product usage questions.

---

## 4. Success Criteria

We should consider the migration successful when:

- Core feature events are visible in PostHog with correct properties.
- We can answer feature-wise questions from PostHog alone.
- The team can view dashboards for adoption, engagement, and conversion without relying on custom backend analytics SQL.
- The current frontend analytics wrapper sends events consistently through a single PostHog-backed implementation.
- Opt-in / privacy behavior is defined and enforced consistently.

---

## 5. Scope

### In Scope

- Frontend PostHog integration in Next.js.
- Standardized event naming and property schema.
- User/session identification strategy.
- Product dashboards in PostHog.
- Migration of current custom analytics calls to a PostHog-backed client wrapper.
- Optional transitional backend forwarding if we want temporary dual-write.

### Out of Scope

- Replacing server logs or infrastructure monitoring.
- Billing analytics and financial reconciliation.
- Rewriting every historical analytics event immediately.
- Full warehouse sync in the first phase.

---

## 6. Current-State Assessment

### Frontend

Current state:

- `frontend/src/lib/analytics.ts` already centralizes analytics calls, which is good.
- Many methods are stubbed or return placeholders.
- Event naming already exists in several components, but property shape is inconsistent.
- Some logic skips analytics entirely in localhost/dev.

Implication:

- We already have the right seam for migration.
- We should replace the implementation behind the existing analytics wrapper instead of scattering direct PostHog calls everywhere.

### Backend

Current state:

- `backend/app/api/routers/analytics.py` accepts events and powers a custom admin dashboard.
- This endpoint is useful as a migration bridge, but not the long-term analytics strategy.

Implication:

- We do not need to expand this backend unless we choose temporary dual-write or server-side analytics for specific events.

---

## 7. Target Architecture

### Primary Model

Use PostHog client-side for product events generated from the web app.

### Secondary Model

Use server-side PostHog capture only for events that are:

- created purely on the backend,
- business-critical to trust from the server,
- or detached from a browser session.

Examples:

- background job completed,
- recap email sent,
- calendar sync completed on backend only,
- payment confirmed.

### Wrapper Strategy

Keep `frontend/src/lib/analytics.ts` as the app-facing API, but change its internals to use PostHog.

Benefits:

- Minimal churn across the codebase.
- Existing `Analytics.track(...)` callsites remain valid.
- Easier phased migration.

---

## 8. Recommended Event Design

### Naming Principles

- Use snake_case.
- Prefer clear business events over generic button-click spam.
- Track outcomes as well as intent.
- Keep event names stable; add detail in properties.

### Core Identity Properties

Every event should include as many of these as are safely available:

- `user_email`
- `workspace_id`
- `meeting_id`
- `session_id`
- `app_version`
- `environment`
- `source`

### Feature Event Set

#### Recording / Meeting Lifecycle

- `meeting_started`
- `recording_started`
- `recording_stopped`
- `meeting_completed`
- `meeting_deleted`

Suggested properties:

- `meeting_id`
- `title_length`
- `duration_seconds`
- `source`

#### Notes / Summaries

- `notes_generation_started`
- `notes_generated`
- `notes_regenerated`
- `notes_template_switched`
- `notes_refined`
- `notes_shared`
- `shared_note_viewed`

Suggested properties:

- `meeting_id`
- `template_name`
- `provider`
- `model`
- `duration_seconds`
- `success`
- `share_method`
- `recipient_count`

#### AI / Search / Context

- `ask_ai_submitted`
- `catch_up_requested`
- `cross_meeting_search_used`
- `cross_meeting_search_result_clicked`
- `ai_host_suggestion_shown`
- `ai_host_suggestion_pinned`
- `ai_host_suggestion_dismissed`

Suggested properties:

- `meeting_id`
- `query_length`
- `result_count`
- `source`
- `suggestion_type`

#### Calendar / Integrations

- `calendar_connect_started`
- `calendar_connect_completed`
- `calendar_sync_completed`
- `calendar_recap_sent`

Suggested properties:

- `provider`
- `request_write_scope`
- `success`
- `recipient_count`

#### Settings / Preferences

- `settings_changed`
- `analytics_enabled`
- `analytics_disabled`
- `model_changed`

Suggested properties:

- `setting_type`
- `old_value`
- `new_value`

### Events To Avoid As First-Class Metrics

- Generic `button_click` for every UI click.
- Raw page view spam unless tied to meaningful screens.
- Any event that duplicates the same business outcome at multiple layers.

---

## 9. Identity Strategy

### Distinct ID

Recommended default:

- Use user email as the distinct identifier for authenticated users.
- Use a generated anonymous ID before login.
- Alias anonymous usage to the authenticated identity on sign-in.

### People Properties

Set durable user properties in PostHog for:

- `email`
- `name`
- `workspace_count` when available
- `calendar_connected`
- `first_seen_at`
- `plan_tier` if introduced later

### Caution

If we do not want to store raw email in PostHog, we should hash it before sending and keep the raw value out of analytics entirely. This is a product/privacy decision we should make before implementation.

---

## 10. Privacy, Consent, and Compliance

Before implementation, we should settle these decisions explicitly:

- Do we default analytics to opt-in or opt-out?
- Do we send raw email or a hashed identifier?
- Do we allow session replay in all environments?
- Do we redact transcript/note content from analytics completely?

Recommended default posture:

- Never send transcript text, note bodies, prompts, or AI outputs as analytics properties.
- Send metadata only, such as lengths, counts, template names, model names, and success/failure.
- Gate analytics initialization behind the existing user preference.
- Start with session replay disabled or heavily masked until reviewed.

---

## 11. Migration Strategy

### Phase 1: Planning and Schema Freeze

- Approve event naming conventions.
- Decide identity/privacy policy.
- Decide whether we want temporary dual-write.
- Define the first dashboard set in PostHog.

### Phase 2: Foundation

- Add PostHog client dependency to the frontend.
- Add environment variables for PostHog project key and host.
- Rework `frontend/src/lib/analytics.ts` to initialize PostHog and capture events.
- Keep the public interface of `Analytics` stable where possible.

### Phase 3: Core Event Migration

- Wire the highest-value existing calls first:
  - recording lifecycle,
  - notes generation,
  - note sharing,
  - calendar connect,
  - AI feature usage.
- Replace placeholder methods with real PostHog-backed behavior or remove them.

### Phase 4: Dashboard and Validation

- Build PostHog dashboards for:
  - active users,
  - notes generation funnel,
  - feature adoption by event,
  - template usage,
  - note sharing conversion,
  - calendar connection conversion.
- Validate event counts against real user flows.

### Phase 5: Decommission Legacy Analytics

- Stop treating the custom backend analytics dashboard as primary.
- Optionally keep backend event ingestion for a short sunset period.
- Remove dead analytics code paths and stale DB analytics assumptions.

---

## 12. Proposed Dashboard Set In PostHog

### Dashboard A: Product Adoption

- Weekly active users.
- Meetings started.
- Notes generated.
- Notes shared.
- Calendar connected.

### Dashboard B: Feature Usage

- Ask AI usage.
- Catch Up usage.
- AI host suggestion interactions.
- Template switching frequency.
- Model/provider usage.

### Dashboard C: Conversion / Funnel

- `meeting_started`
- `recording_stopped`
- `notes_generated`
- `notes_shared`

### Dashboard D: Retention / Stickiness

- Returning users by week.
- Users who generated notes at least once and returned.
- Users who connected calendar and then shared notes.

---

## 13. Implementation Tasks

### Frontend

- Add PostHog SDK.
- Create config wiring from env.
- Refactor `frontend/src/lib/analytics.ts` into a PostHog-backed adapter.
- Update `AnalyticsProvider` to initialize PostHog safely.
- Audit every current analytics callsite and map it to approved events.
- Remove or rewrite placeholder analytics methods.

### Backend

- Decide whether to keep `/analytics/track` during migration.
- Optionally add a small server-side analytics helper for backend-only events.
- Decide whether `/analytics/dashboard/metrics` stays temporarily or is deprecated.

### Product / Ops

- Create PostHog project and environments.
- Configure env vars for local, staging, and production.
- Decide replay/masking/privacy settings.
- Create initial dashboards and saved insights.

---

## 14. Environment Variables

Frontend:

- `NEXT_PUBLIC_POSTHOG_KEY`
- `NEXT_PUBLIC_POSTHOG_HOST`
- `NEXT_PUBLIC_APP_ENV`

Optional backend:

- `POSTHOG_PROJECT_API_KEY`
- `POSTHOG_HOST`

---

## 15. Rollout Plan

### Step 1

Ship PostHog integration behind the existing analytics preference toggle.

### Step 2

Dual-run a limited set of key events for a short validation window, if needed.

### Step 3

Verify:

- events appear in PostHog,
- user identity is stable,
- no sensitive content is leaking,
- dashboards answer the product questions we care about.

### Step 4

Retire or downgrade the custom analytics dashboard.

---

## 16. Risks

### Risk: Event Sprawl

Too many loosely defined events will make dashboards noisy.

Mitigation:

- Freeze a v1 event schema before implementation.

### Risk: Sensitive Data Leakage

Transcript or note content could accidentally be sent as properties.

Mitigation:

- Explicit allowlist of analytics properties.
- No raw content fields in event payloads.

### Risk: Double Counting During Migration

Dual-write can produce confusion if not labeled.

Mitigation:

- Keep the validation window short.
- Compare only a small set of canonical events.

### Risk: Identity Fragmentation

Anonymous and authenticated activity may split into different users.

Mitigation:

- Define alias/identify behavior up front.

---

## 17. Open Decisions Before Implementation

1. Should PostHog receive raw email, or should we hash identifiers?
2. Do we want to keep the existing `/dashboard` page temporarily, or point the team directly to PostHog?
3. Do we need backend/server-side event capture in v1, or is frontend-only enough for the first milestone?
4. Do we want session replay in production at launch, or postpone it until privacy review?
5. Do we want one global PostHog project or separate staging and production projects?

---

## 18. Recommended First Milestone

The first milestone should be intentionally small:

- Integrate PostHog in the frontend.
- Migrate 8-12 high-value events.
- Capture authenticated identity safely.
- Build 3 dashboards:
  - feature adoption,
  - notes funnel,
  - calendar conversion.

This gives us usable product analytics quickly without overcommitting to a full rewrite on day one.

---

## 19. Recommendation Summary

Use PostHog as the primary product analytics platform.

Do not continue investing in the custom analytics architecture as the long-term system for feature analytics. Treat the current backend analytics code as transitional infrastructure only, and migrate through the existing frontend `Analytics` wrapper so implementation stays controlled and low-risk.
