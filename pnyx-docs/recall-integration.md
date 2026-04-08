# Recall.ai Integration for Pnyx

This document describes the integration of **Recall.ai** to enable Pnyx to join and transcribe online meetings (Zoom, Google Meet, Microsoft Teams) via a headless bot participant.

---

## 🏗️ Architecture Overview

The Pnyx Recall.ai integration supports two entry points:

1.  **📅 Calendar Auto-Join**:
    - The `CalendarReminderScheduler` syncs the user's Google Calendar periodically.
    - If a meeting has a valid meeting URL and **"Auto-Join Bot"** is enabled in settings, Pnyx spawns a bot ~2 minutes before start.
2.  **🚀 Ad-hoc Join**:
    - User pastes a meeting URL into the Pnyx dashboard → bot spawns immediately.

### Technical Flow
1.  **Bot Spawning**: Join request initiated (Auto or Ad-hoc). Quota validated (5hr/week).
2.  **Recall.ai API**: Backend calls Recall.ai to spawn a bot named "Pnyx AI Assistant".
3.  **Bot Joins**: Recall spins up a headless browser and joins the meeting.
4.  **Audio & Transcription**: Bot captures all participant audio. Recall performs real-time transcription.
5.  **Webhook Streaming**: Recall sends transcript segments to `POST /api/bot/webhook`.
6.  **Real-Time Sync**:
    - Backend verifies webhook signature, stores segments with **speaker names** from the meeting platform.
    - Broadcasts to frontend via **Redis Pub/Sub → WebSockets**.
    - Each "final" segment triggers the **AI Participant** (`ingest_transcript_host`) for live decisions/actions.
7.  **Ongoing Meeting UI**: If a user opens Pnyx during a bot session, they see:
    - Live-scrolling Transcript (synced via Webhooks → WebSockets).
    - Real-time AI Participant suggestions (Decisions, Action Items, Insights).
    - Notes Generation status, matching the exact experience of a standard local recording.
8.  **End-of-Meeting**: When the bot leaves:
    - `meeting_bots.status` → `completed`, `duration_seconds` calculated.
    - Full transcript finalized → Notes generation triggered automatically.
    - WebSocket broadcast channel closed.

---

## 🛡️ Production Reliability & Scaling

1.  **Webhook Signature Verification**: Incoming webhooks are validated against `RECALL_WEBHOOK_SECRET` via the `X-Recall-Signature` header to prevent spoofed payloads.
2.  **Webhook Idempotency**: `recall_bot_id` + `segment_index` checked before insert — safe for retries.
3.  **Transcript Ordering**: Segments sorted by `start_time` and `sequence_id` for correct conversation flow even if packets arrive out of order.
4.  **Speaker Diarization**: Speaker names extracted from Recall's payload (sourced from the meeting platform UI) and mapped directly to `transcript_segments.speaker`.
5.  **WebSocket Scaling**: Redis Pub/Sub ensures multiple backend workers can broadcast to all active listeners.

---

## 📈 Usage Quotas & Rate Limiting

| Limit | Value |
|-------|-------|
| Weekly bot usage per user | **5 hours** |
| Quota reset | Rolling 7-day window |

- **Enforcement**: `RecallManager` sums `duration_seconds` from `meeting_bots` for the user in the last 7 days before spawning.
- **UI Feedback**: Users exceeding the limit see a clear error message with their remaining quota.

---

## ⚙️ Configuration

Add to `backend/.env`:

```env
# Recall.ai
RECALL_API_KEY=your_recall_api_key_here
RECALL_REGION=us-west-2
RECALL_WEBHOOK_SECRET=your_webhook_signing_secret
RECALL_WEBHOOK_URL=https://your-public-tunnel.pinggy.link/api/bot/webhook
```

---

## 🛠️ Implementation Details

### 1. Database
**`meeting_bots` table** tracks active and historical bot sessions:
- `meeting_id`, `recall_bot_id`, `user_email`, `meeting_url`
- `status`: `requesting` → `joining` → `recording` → `completed` / `fatal`
- `duration_seconds`: Actual recording duration (used for quota).
- `error_message`: Stores details if the bot fails.

### 2. Backend Services

| Service | File | Purpose |
|---------|------|---------|
| `RecallClient` | `services/recall/client.py` | Low-level Recall API wrapper (spawn, status, leave) |
| `RecallManager` | `services/recall/manager.py` | Quota check, spawn orchestration, webhook processing, end-of-meeting finalization |

### 3. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/meetings/{id}/invite-bot` | Spawn bot with a meeting URL |
| `DELETE` | `/api/meetings/{id}/bot` | Remove bot from meeting mid-session |
| `POST` | `/api/bot/webhook` | Public — receives Recall.ai events |

### 4. Frontend
- **Online Meeting Join**: URL input + "Send Bot" button.
- **Bot Status HUD**: Real-time status indicator.
- **"Remove Bot" button**: Calls `DELETE /api/meetings/{id}/bot`.
- **Settings Toggle**: "Auto-Join Bot to Calendar Meetings".

---

## 🚦 Troubleshooting

### Bot Not Joining
- **Waiting Rooms**: A human must **"Admit"** the bot.
- **Region Mismatch**: Ensure `RECALL_REGION` matches the meeting's hosting region.

### Missing Live Transcripts
- **Tunnel Down**: Check if Pinggy/Ngrok is active.
- **Webhook URL**: Verify `RECALL_WEBHOOK_URL` is publicly accessible HTTPS.
- **API Key Features**: Ensure "Real-time Transcription" is enabled in Recall dashboard.

### Quota Exceeded
- Users can check remaining quota in the Settings page.
- Quota resets on a rolling 7-day window — no manual reset needed.
