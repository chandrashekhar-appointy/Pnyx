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

## Auth Architecture (no re-authentication needed)

| Need | How |
|---|---|
| Google Calendar access | `chrome.identity.getAuthToken()` — uses Chrome's signed-in Google account, one "Allow" prompt ever |
| Pnyx API calls (from popup) | Session cookie auto-included by browser (user already logged into the site) |
| Opening Pnyx to start recording | Just opens a URL — existing session cookie handles auth |

**Zero separate login required.** If user isn't logged in, the site redirects to Google OAuth (one click since they're already signed into Chrome) and returns to the meeting page.

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

### Phase 1 — Extension scaffold + full notification logic ✅
- [x] Plan doc
- [x] `manifest.json`
- [x] `background.js` — complete state machine, calendar fetch, alarms, notifications
- [x] `popup/` — today's meetings UI with Start buttons
- [x] Frontend: pick up `?calendar_event_id=` from URL and set on meeting context

### Phase 2 — Google Cloud setup + sideload (manual, 30 min)
- [ ] Load extension → get Extension ID
- [ ] Create OAuth client in Google Cloud Console
- [ ] Update `manifest.json` with real client_id
- [ ] Generate icon PNGs (replace placeholders)
- [ ] Test in office: calendar connect → notification → one-click start

### Phase 3 — Recall bot suppression
- [ ] `background.js`: call `/api/meetings?active=true` to detect active bot sessions
- [ ] Suppress notifications when bot is confirmed in-meeting
- [ ] "Add Pnyx Bot" notification for online meetings where bot didn't join

### Phase 4 — Notes ready notification
- [ ] Store `calendar_event_id` on meeting record in backend
- [ ] Background: detect meeting end by event end time + API poll
- [ ] Fire "Notes ready" notification with direct link to meeting details

### Phase 5 — Office rollout
- [ ] Sideload on team laptops (developer mode, load unpacked)
- [ ] QR code with sideload instructions for self-service installs
- [ ] Monitor notification click-through rate via PostHog

---

## Effort Summary

| Phase | Effort | Status |
|---|---|---|
| Phase 1: Scaffold + logic | 8h | ✅ Done |
| Phase 2: Google Cloud + sideload | 30min manual | Pending |
| Phase 3: Recall bot suppression | 3h | Pending |
| Phase 4: Notes ready | 2h | Pending |
| Phase 5: Office rollout | 1h | Pending |
| **Total** | **~14h dev + 30min manual** | |
