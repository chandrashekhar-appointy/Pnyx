-- Migration 026: meeting_bots table for Recall.ai bot session tracking
-- Status flow: requesting → joining → recording → completed | fatal

CREATE TABLE IF NOT EXISTS meeting_bots (
    id               SERIAL PRIMARY KEY,
    meeting_id       TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    recall_bot_id    TEXT NOT NULL UNIQUE,
    user_email       TEXT NOT NULL,
    meeting_url      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'requesting',
    bot_name         TEXT DEFAULT 'Pnyx AI Assistant',
    duration_seconds INTEGER DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMP DEFAULT NOW(),
    updated_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meeting_bots_meeting ON meeting_bots(meeting_id);
CREATE INDEX IF NOT EXISTS idx_meeting_bots_recall ON meeting_bots(recall_bot_id);
CREATE INDEX IF NOT EXISTS idx_meeting_bots_user ON meeting_bots(user_email);
CREATE INDEX IF NOT EXISTS idx_meeting_bots_status ON meeting_bots(status);
