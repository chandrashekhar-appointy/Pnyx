# Pnyx Chrome Extension

Smart meeting reminders for in-room meetings. Reads Google Calendar, notifies you before meetings start, one click to begin recording.

## Sideload in Chrome (no store needed)

1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** → select this `chrome-extension/` folder
4. Note the Extension ID shown under the extension name (32-char string like `abcdefghijklmnopqrstuvwxyzabcdef`)

## Google Cloud Console setup (one-time)

The extension needs a Chrome App OAuth client in the **same Google Cloud project** as Pnyx.

1. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
2. **Create credentials → OAuth 2.0 Client ID**
   - Application type: **Chrome App**
   - Application ID: paste the Extension ID from step above
3. Copy the **Client ID** (ends in `.apps.googleusercontent.com`)
4. Open `manifest.json` → replace `REPLACE_WITH_EXTENSION_OAUTH_CLIENT_ID` with your client ID
5. Go back to `chrome://extensions` → click the **reload** icon on the extension

## First use

Click the extension icon → **Connect Google Calendar** → approve the "read your calendar" permission (shown once, cached forever by Chrome).

The extension will:
- Check your calendar every 10 minutes
- Notify you 10 min before in-room meetings (no Zoom/Meet link)
- Remind again at meeting start time if you haven't started yet
- Give one final reminder 5 min into the meeting
- Send "Notes ready" when the meeting ends (if you recorded it)

## Notification behaviour

| Timing | What you see | Action |
|---|---|---|
| T-10 min | "Weekly Sync · 10 minutes" | [Start Recording] [Remind at start] |
| T+0 | "Weekly Sync just started" | [Start Now] [Skip this meeting] |
| T+5 (last) | "Weekly Sync · 5 min in" | [Start now] [Don't remind me] |
| After meeting | "✓ Weekly Sync — Notes ready" | [View Notes] |

Clicking **Skip** on any notification stops all further reminders for that meeting.

Online meetings (Google Meet / Zoom / Teams) are suppressed when the Pnyx bot is already recording.

## Replacing icons

The current icons are placeholder solid-black PNGs. Replace `icons/16.png`, `icons/48.png`, `icons/128.png` with real Pnyx brand icons, then reload the extension.

## Dev notes

- `background.js` — service worker: all notification logic, state machine, Calendar API calls
- `popup/popup.js` — popup UI: today's events + recent Pnyx meetings
- State stored in `chrome.storage.local` under key `eventStates`
- Events older than 24h are pruned automatically
