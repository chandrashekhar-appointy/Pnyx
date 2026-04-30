-- Migration: Transcript Versioning
-- Purpose: Enable non-destructive transcript updates with version history
-- Date: 2026-01-28

-- Create transcript_versions table
CREATE TABLE IF NOT EXISTS transcript_versions (
  id SERIAL PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  version_num INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('live', 'diarized', 'manual_edit')),
  content_json JSONB NOT NULL,
  is_authoritative BOOLEAN DEFAULT FALSE,

  -- Metadata for debugging and auditing
  created_at TIMESTAMP DEFAULT NOW(),
  created_by TEXT DEFAULT 'system',
  alignment_config JSONB,
  confidence_metrics JSONB,

  -- Ensure one version number per meeting
  UNIQUE(meeting_id, version_num)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_transcript_versions_meeting
  ON transcript_versions(meeting_id);

CREATE INDEX IF NOT EXISTS idx_transcript_versions_auth
  ON transcript_versions(meeting_id, is_authoritative)
  WHERE is_authoritative = TRUE;

CREATE INDEX IF NOT EXISTS idx_transcript_versions_source
  ON transcript_versions(meeting_id, source);

-- Add comments for documentation
COMMENT ON TABLE transcript_versions IS 'Stores historical versions of transcripts for non-destructive updates';
COMMENT ON COLUMN transcript_versions.version_num IS 'Auto-incrementing version number per meeting';
COMMENT ON COLUMN transcript_versions.source IS 'Origin: live (real-time), diarized (post-processed), manual_edit (user edited)';
COMMENT ON COLUMN transcript_versions.is_authoritative IS 'TRUE for the currently active version shown in UI';
COMMENT ON COLUMN transcript_versions.alignment_config IS 'Stores alignment algorithm settings used (for debugging)';
COMMENT ON COLUMN transcript_versions.confidence_metrics IS 'Stats: avg_confidence, uncertain_count, overlap_count, method_breakdown';
