-- Migration 029: Recreate analytics_events
--
-- Migration 021 dropped analytics_events as "redundancy cleanup" (analytics
-- were meant to move to PostHog), but the in-app admin dashboard and the
-- /analytics/track endpoint still depend on it, and PostHog was never reliably
-- wired in prod. We self-host analytics again so the dashboard works in every
-- environment without external config.

CREATE TABLE IF NOT EXISTS analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(255),
    user_id VARCHAR(255),
    event_name VARCHAR(255) NOT NULL,
    properties JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_name ON analytics_events(event_name);
CREATE INDEX IF NOT EXISTS idx_analytics_events_user ON analytics_events(user_id);
CREATE INDEX IF NOT EXISTS idx_analytics_events_time ON analytics_events(timestamp);
