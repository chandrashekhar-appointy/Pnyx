-- Migration 019: Aggregate credit usage per meeting instead of ledger rows per batch

CREATE TABLE IF NOT EXISTS meeting_credit_usage (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id          VARCHAR(255) NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    user_email          VARCHAR(255) NOT NULL,
    credits_used        INTEGER NOT NULL DEFAULT 0,
    last_balance_after  INTEGER,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at            TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(meeting_id, user_email)
);

CREATE INDEX IF NOT EXISTS idx_meeting_credit_usage_user
    ON meeting_credit_usage (user_email);

CREATE INDEX IF NOT EXISTS idx_meeting_credit_usage_meeting
    ON meeting_credit_usage (meeting_id);

CREATE INDEX IF NOT EXISTS idx_meeting_credit_usage_updated
    ON meeting_credit_usage (updated_at DESC);
