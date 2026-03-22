-- Migration 020: Drop old inline content columns after bucket migration

ALTER TABLE full_transcripts
    DROP COLUMN IF EXISTS transcript_text;

ALTER TABLE transcript_versions
    DROP COLUMN IF EXISTS content_json;

ALTER TABLE summary_processes
    DROP COLUMN IF EXISTS result;
