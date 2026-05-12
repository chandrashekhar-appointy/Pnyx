-- Migration: Diarization support + transcript source column
-- Purpose: Consolidate the legacy Python-only migrations
--          (add_diarization_support.py, add_transcript_versioning.py)
--          into a numbered SQL migration so production picks them up
--          via apply_sql_migrations.py.
-- Date: 2026-05-12

-- transcript_segments: speaker / source columns referenced by db.manager
ALTER TABLE transcript_segments
    ADD COLUMN IF NOT EXISTS speaker TEXT DEFAULT NULL;

ALTER TABLE transcript_segments
    ADD COLUMN IF NOT EXISTS speaker_confidence REAL DEFAULT NULL;

ALTER TABLE transcript_segments
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'live';

-- meetings: diarization tracking columns
ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS diarization_status TEXT DEFAULT 'pending';

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS audio_recorded BOOLEAN DEFAULT FALSE;

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS diarization_provider TEXT DEFAULT NULL;

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS diarization_completed_at TIMESTAMP DEFAULT NULL;

-- audio_chunks metadata table
CREATE TABLE IF NOT EXISTS audio_chunks (
    id SERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    start_time_seconds REAL,
    end_time_seconds REAL,
    duration_seconds REAL,
    size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(meeting_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_audio_chunks_meeting_id ON audio_chunks(meeting_id);

-- meeting_speakers mapping (for speaker renaming)
CREATE TABLE IF NOT EXISTS meeting_speakers (
    id SERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    diarization_label TEXT NOT NULL,
    display_name TEXT DEFAULT NULL,
    color TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(meeting_id, diarization_label)
);

CREATE INDEX IF NOT EXISTS idx_meeting_speakers_meeting_id ON meeting_speakers(meeting_id);

-- speaker_profiles (future voice enrollment)
CREATE TABLE IF NOT EXISTS speaker_profiles (
    id SERIAL PRIMARY KEY,
    workspace_id TEXT,
    display_name TEXT NOT NULL,
    email TEXT,
    voice_embedding BYTEA DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- diarization_jobs (background job tracking)
CREATE TABLE IF NOT EXISTS diarization_jobs (
    id SERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    provider TEXT NOT NULL DEFAULT 'deepgram',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    speaker_count INTEGER,
    segment_count INTEGER,
    processing_time_seconds REAL,
    error_message TEXT,
    result_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_diarization_jobs_meeting_id ON diarization_jobs(meeting_id);
CREATE INDEX IF NOT EXISTS idx_diarization_jobs_status ON diarization_jobs(status);
