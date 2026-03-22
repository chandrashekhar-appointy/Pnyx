-- Migration 018: Move large transcript and notes payloads to object storage references

ALTER TABLE full_transcripts
    ADD COLUMN IF NOT EXISTS transcript_object_path TEXT,
    ADD COLUMN IF NOT EXISTS transcript_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS transcript_byte_size BIGINT,
    ADD COLUMN IF NOT EXISTS transcript_preview TEXT;

ALTER TABLE full_transcripts
    ALTER COLUMN transcript_text DROP NOT NULL;

ALTER TABLE transcript_versions
    ADD COLUMN IF NOT EXISTS content_object_path TEXT,
    ADD COLUMN IF NOT EXISTS content_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS content_byte_size BIGINT;

ALTER TABLE transcript_versions
    ALTER COLUMN content_json DROP NOT NULL;

ALTER TABLE summary_processes
    ADD COLUMN IF NOT EXISTS result_object_path TEXT,
    ADD COLUMN IF NOT EXISTS result_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS result_byte_size BIGINT,
    ADD COLUMN IF NOT EXISTS result_preview TEXT;
