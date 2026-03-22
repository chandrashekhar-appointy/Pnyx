-- Migration: Optimized Indexes for Scaling
-- Purpose: Speed up session reconciliation and recent data retrieval
-- Date: 2026-03-21

-- Index for chronological chunk retrieval (useful for session reconciler)
CREATE INDEX IF NOT EXISTS idx_recording_chunks_chronological
  ON recording_chunks(session_id, created_at DESC);

-- Index for session heartbeat monitoring
CREATE INDEX IF NOT EXISTS idx_recording_sessions_heartbeat
  ON recording_sessions(last_heartbeat_at)
  WHERE status = 'recording';

-- Index for faster transcript retrieval by meeting
-- (Complementary to the existing time_range index)
CREATE INDEX IF NOT EXISTS idx_transcript_segments_meeting_id
  ON transcript_segments(meeting_id, created_at DESC);
