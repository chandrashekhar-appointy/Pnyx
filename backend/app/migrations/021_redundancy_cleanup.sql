-- Migration: Redundancy Cleanup
-- Purpose: Remove obsolete tables and analytics logic as requested
-- Date: 2026-03-21

-- 1. Drop 100% obsolete table (no code references)
DROP TABLE IF EXISTS alignment_states;

-- 2. Drop redundant analytics table (functionality moved to PostHog)
-- Note: This will break the internal admin dashboard if not also removed from code.
DROP TABLE IF EXISTS analytics_events;

-- 3. Cleanup stale OAuth states (optional but recommended)
DELETE FROM calendar_oauth_states WHERE created_at < NOW() - INTERVAL '24 hours';
