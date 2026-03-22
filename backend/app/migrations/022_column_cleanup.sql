-- Migration: Column Cleanup (Step 2)
-- Purpose: Remove legacy AI fields and redundant metadata for better performance/clarity
-- Date: 2026-03-21

-- 1. transcript_segments: Remove legacy Meetily columns
ALTER TABLE transcript_segments 
  DROP COLUMN IF EXISTS summary,
  DROP COLUMN IF EXISTS action_items,
  DROP COLUMN IF EXISTS key_points,
  DROP COLUMN IF EXISTS duration;

-- 2. summary_processes: Remove result_preview (moved to buckets)
ALTER TABLE summary_processes
  DROP COLUMN IF EXISTS result_preview;

-- 3. full_transcripts: Remove redundant meeting_name
ALTER TABLE full_transcripts
  DROP COLUMN IF EXISTS meeting_name;

-- 4. diarization_chunk_jobs: Cleanup (Optional audit check)
-- No columns found for removal, but noting that job_id is PRIMARY KEY.
