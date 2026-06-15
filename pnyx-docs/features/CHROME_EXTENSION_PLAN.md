# Pnyx Chrome Extension — Implementation Plan

**Status**: Phase 1 In Progress  
**Target**: Zero-friction in-room meeting recording via Google Calendar + smart notifications  
**Frontend URL**: `https://frontend-dev-350906.bifrost.saastack.site`

---

## What This Builds

A Chrome extension that:
1. Reads the user's Google Calendar (one-time auth, silent refresh forever)
2. Detects upcoming in-room meetings (no video link = in-room)
3. Sends smart notifications at the right time — not too early, not annoying
4. "Start Recording" in one click → Pnyx opens with title pre-filled + auto-starts
5. Suppresses notifications when Pnyx bot is already in the meeting (online meetings)
6. Sends a positive "Notes ready" notification when the meeting ends

---

## Auth Architecture (CORRECTED after codebase audit)

> ⚠️ The first draft of this plan assumed the extension could call the Pnyx
> backend with an auto-included session cookie. **That is false.** The backend
> (`app/api/deps.py` → `verify_google_token`) authenticates a Google **ID token**
> (JWT, `aud` = the web OAuth client ID) sent as `Authorization: Bearer`. It does
> NOT use cookies, and the frontend has no `/api/*` proxy to the backend. So a
> browser extension cannot trivially call the backend.

**The design decision: decouple.** The extension is fully self-contained and
needs NO backend connection for its core value.

| Need | How | Backend auth? |
|---|---|---|
| Google Calendar access | `chrome.identity.getAuthToken()` — Chrome-managed token, one "Allow" prompt ever, auto-refresh | No |
| Fire reminders | `chrome.alarms` + `chrome.notifications` (all local) | No |
| One-click "Start Recording" | Opens a frontend URL (`/?autoStart=true&...`); the **frontend's** existing NextAuth session authenticates everything | No |
| "Already recording" suppression | Heuristic: a Pnyx tab open during the meeting window = user is on it, don't nag | No |
| Cross-device suppression / recent meetings | **Optional, disabled by default.** Needs an authenticated backend channel (see Phase 3 spike) | Yes |

**Zero separate login for the core flow.** If the user isn't logged into Pnyx
when "Start Recording" opens the site, the site redirects to Google OAuth (one
click since they're already signed into Chrome) and returns to the meeting page.

### Why NOT `launchWebAuthFlow` for the backend channel

The "get an ID token with `aud` = web client via implicit flow" trick was
rejected: Google has been deprecating the implicit (`response_type=id_token`)
flow, and silent hourly refresh in a background service worker is fragile. The
robust path (when we build it) is: reuse the **already-working** `getAuthToken`
access token + a small backend change to validate opaque access tokens via
Google's `tokeninfo`/`userinfo` endpoint (check `aud` against an allowlist,
extract email) as a fallback in `verify_google_token`. This is gated on a
20-minute spike against the deployed backend and is its own workstream.

---

## Notification State Machine

Every calendar event gets its own state tracked in `chrome.storage.local`.

```
PENDING
  ↓ T-10 min (not dismissed, not online+bot, not already recording)
REMINDED_T10
  ↓ no action by T+0
REMINDED_START  ← "Meeting just started"
  ↓ no action by T+5
REMINDED_FINAL  ← last notification, no more after this
  ↓ no action by T+10
EXPIRED (silent)

Any state → STARTED  (user clicked Start, or Pnyx tab detected, or active meeting found)
Any state → DISMISSED (user clicked Skip / Don't remind me)
STARTED → NOTES_READY (at meeting end time + 2min)
```

**Max 3 notifications per meeting. Clicking Skip ends all notifications for that event forever.**

---

## Smart Suppression Rules

Do NOT notify when any of these are true:

| Condition | Detection |
|---|---|
| Recall bot already in the meeting | `GET /api/meetings?active=true` → match by time window |
| User already opened Pnyx | `chrome.tabs.query({url: "https://frontend-dev-350906.bifrost.saastack.site/*"})` |
| Active Pnyx meeting within ±15 min of event start | Same API call above |
| All-day event | `event.start.date` exists (not `dateTime`) |
| Event marked Free | `event.transparency === "transparent"` |
| User declined the event | `attendee.self.responseStatus === "declined"` |
| Solo event (no other attendees) | `attendees.filter(a => !a.self).length === 0` |
| Already dismissed for this event | State in storage is `DISMISSED` or `EXPIRED` |

---

## Notification Content

**T-10 (pre-meeting nudge):**
```
[Pnyx]  Weekly Sync · 10 minutes
        In-room meeting · Conference Room 1
        [🎙 Start Recording]   [Remind at start time]
```

**T+0 (meeting started):**
```
[Pnyx]  Weekly Sync just started
        Don't miss it — start recording now
        [🎙 Start Now]   [Skip this meeting]
```

**T+5 (last chance):**
```
[Pnyx]  Weekly Sync · 5 min in
        Last reminder to start recording
        [Start now]   [Don't remind me]
```

**Online meeting — bot not joined:**
```
[Pnyx]  Design Review (Google Meet) · 5 min
        Pnyx bot isn't in this meeting
        [Add Pnyx Bot]   [Skip]
```

**Notes ready (positive close):**
```
[Pnyx]  ✓ Weekly Sync — Notes ready
        AI notes, transcript and action items waiting
        [View Notes]
```

---

## Extension Popup

```
┌─────────────────────────────────────┐
│  🎙 Pnyx                     ⚙      │
├─────────────────────────────────────┤
│  TODAY                              │
│                                     │
│  ● 3:00 PM  Weekly Sync             │
│    In-room · starts in 8 min        │
│    [Start Recording]                │
│                                     │
│  ○ 4:30 PM  Design Review          │
│    Google Meet · bot will join      │
│                                     │
│  ○ 6:00 PM  1:1 with Rajan         │
│    In-room                          │
│    [Start Recording]                │
├─────────────────────────────────────┤
│  RECENT MEETINGS                    │
│  Yesterday · Sprint Planning  →     │
│  Mon · Product Sync           →     │
└─────────────────────────────────────┘
```

Green dot = in progress, grey = upcoming, check = done with notes.

---

## Online vs In-Room Decision Tree

```
Calendar event detected
        ↓
Has video link? (Meet / Zoom / Teams)
    YES                       NO
     ↓                         ↓
Check Recall bot          In-room → notification ladder
status via API
     ↓
Bot active?
  YES          NO
   ↓             ↓
Suppress     Single notification:
all notifs   "Add Pnyx Bot?"
   ↓
At end time:
"Notes ready"
```

---

## File Structure

```
chrome-extension/
├── manifest.json          — MV3, permissions, OAuth client ID
├── background.js          — service worker: full state machine
├── popup/
│   ├── popup.html
│   ├── popup.js           — today's meetings + recent Pnyx meetings
│   └── popup.css
├── icons/
│   ├── 16.png
│   ├── 48.png
│   └── 128.png
└── README.md              — sideload + Google Cloud setup instructions
```

---

## Google Cloud Console Setup (one-time, manual)

The extension needs its own OAuth 2.0 client ID but in the **same Google Cloud project** as the Pnyx web app. Same project = same consent screen = no extra "authorize this app" prompt for users who've already logged into Pnyx.

Steps:
1. Load the extension in Chrome developer mode → note the Extension ID (32-char string)
2. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
3. Create credentials → OAuth 2.0 Client ID → type: **Chrome App**
4. Application ID: paste the Extension ID from step 1
5. Copy the resulting Client ID → paste into `manifest.json` under `oauth2.client_id`
6. Reload the extension

Scopes needed: `https://www.googleapis.com/auth/calendar.readonly` only.

---

## URL Protocol (extension → frontend)

When extension opens Pnyx to start recording:
```
https://frontend-dev-350906.bifrost.saastack.site/?autoStart=true
  &meetingTitle=Weekly+Sync
  &source=extension
  &calendar_event_id=<google_calendar_event_id>
```

- `autoStart=true` — already implemented, starts recording after 3s countdown
- `meetingTitle=` — already implemented, pre-fills meeting name
- `source=extension` — cleaned from URL after use (analytics only)
- `calendar_event_id=` — **new**: stored on meeting record for "notes ready" detection

---

## Build Phases

### Phase 1 — Extension scaffold + notification logic ✅ DONE
- [x] Plan doc
- [x] `manifest.json` (MV3, Calendar scope, minimal host permissions)
- [x] `background.js` — complete state machine, calendar fetch, alarms, notifications
- [x] `popup/` — today's meetings UI with Start buttons
- [x] Frontend: pick up `?calendar_event_id=` from URL and set on meeting context

### Phase 2 — Decouple from backend + harden ✅ DONE
- [x] Removed broken `/api/meetings` cookie calls (backend uses Bearer ID token, separate origin)
- [x] "Already recording" suppression via Pnyx-tab heuristic (backend-free, reliable)
- [x] Notes-ready notification fires locally at meeting end (links to app)
- [x] Recent-meetings section degrades gracefully (hidden until backend channel exists)
- [x] Popup ↔ background state sync via `chrome.runtime.sendMessage`
- [x] `getAuthToken` 401 → token refresh handling
- [x] Placeholder icons generated

### Phase 3 — (MANUAL, ~30 min) Google Cloud + sideload + office test
- [ ] Load unpacked extension → copy the Extension ID
- [ ] Create a **Chrome App** OAuth client in Google Cloud Console (same project as Pnyx)
- [ ] Paste its client ID into `manifest.json` (replace the placeholder) → reload
- [ ] Connect calendar → verify a test meeting fires the T-10 notification → click Start
- [ ] (Optional) Replace placeholder icons with Pnyx brand icons

### Phase 4 — (OPTIONAL, future) Authenticated backend channel
Gated on a 20-min spike: can the deployed backend accept a token the extension
can obtain? Unlocks cross-device suppression + recent-meetings + bot-presence refinement.
- [ ] Spike: `getAuthToken` access token → does deployed backend accept it?
- [ ] Backend: add access-token validation fallback in `verify_google_token` (Google `tokeninfo`, `aud` allowlist)
- [ ] Set `BACKEND_ORIGIN` in `background.js` + `popup.js`; add it to `host_permissions`
- [ ] Implement `findActivePnyxMeetingViaBackend` (call `/meetings/active-bot-sessions`)
- [ ] Implement `loadRecentMeetings` (call `/get-meetings`)
- [ ] "Add Pnyx Bot" notification for online meetings where the bot is absent

### Phase 5 — Office rollout
- [ ] Multi-machine install: pack a `.crx` or add a shared `key` to manifest so the
      Extension ID (and thus the OAuth client binding) is stable across machines
- [ ] Sideload on team laptops / share install instructions
- [ ] Monitor notification → start conversion (the `source=extension` URL param feeds PostHog)

---

## Effort Summary

| Phase | Effort | Status |
|---|---|---|
| Phase 1: Scaffold + logic | 8h | ✅ Done |
| Phase 2: Decouple + harden | 3h | ✅ Done |
| Phase 3: Google Cloud + sideload | ~30 min manual | ⏳ Needs you |
| Phase 4: Backend channel (optional) | spike + ~4h | Deferred |
| Phase 5: Office rollout | ~1h | Pending |
