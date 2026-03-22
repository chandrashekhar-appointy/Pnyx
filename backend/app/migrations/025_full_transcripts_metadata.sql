-- Migration 025: Add metadata column to full_transcripts for encryption info
-- Purpose: Store encryption wrappers for full transcripts
-- Date: 2026-03-22

ALTER TABLE full_transcripts
    ADD COLUMN IF NOT EXISTS metadata JSONB;

COMMENT ON COLUMN full_transcripts.metadata IS 'Stores encryption metadata (wrappers, nonces) for the full transcript';
