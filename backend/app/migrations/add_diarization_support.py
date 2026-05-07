"""
Database Migration: Add Speaker Diarization Support

This migration adds the necessary tables and columns to support
speaker diarization functionality.

Run this script to apply the migration:
    python -m app.migrations.add_diarization_support

Or import and call apply_migration() directly.
"""

import asyncio
import asyncpg
import os
import logging

logger = logging.getLogger(__name__)


# Migration SQL statements
MIGRATION_SQL = """
-- ============================================
-- Migration: Add Speaker Diarization Support
-- Date: January 25, 2026
-- Description: Adds tables and columns for speaker diarization
-- ============================================

-- 1. Add speaker column to transcript_segments
ALTER TABLE transcript_segments 
ADD COLUMN IF NOT EXISTS speaker TEXT DEFAULT NULL;

-- 2. Add speaker_confidence to track how confident we are in speaker assignment
ALTER TABLE transcript_segments 
ADD COLUMN IF NOT EXISTS speaker_confidence REAL DEFAULT NULL;

-- 3. Add diarization_status to meetings table
ALTER TABLE meetings 
ADD COLUMN IF NOT EXISTS diarization_status TEXT DEFAULT 'pending';

-- 4. Add audio_recorded flag to meetings
ALTER TABLE meetings 
ADD COLUMN IF NOT EXISTS audio_recorded BOOLEAN DEFAULT FALSE;

-- 5. Add diarization_provider to track which service was used
ALTER TABLE meetings 
ADD COLUMN IF NOT EXISTS diarization_provider TEXT DEFAULT NULL;

-- 6. Add diarization_completed_at timestamp
ALTER TABLE meetings 
ADD COLUMN IF NOT EXISTS diarization_completed_at TIMESTAMP DEFAULT NULL;

-- 7. Create audio_chunks metadata table
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

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_audio_chunks_meeting_id ON audio_chunks(meeting_id);

-- 8. Create meeting_speakers mapping table (for speaker renaming)
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

-- Create index for meeting_speakers
CREATE INDEX IF NOT EXISTS idx_meeting_speakers_meeting_id ON meeting_speakers(meeting_id);

-- 9. Create speaker_profiles table (for future voice enrollment)
CREATE TABLE IF NOT EXISTS speaker_profiles (
    id SERIAL PRIMARY KEY,
    workspace_id TEXT,
    display_name TEXT NOT NULL,
    email TEXT,
    voice_embedding BYTEA DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. Create diarization_jobs table (for tracking background jobs)
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

-- Create index for diarization_jobs
CREATE INDEX IF NOT EXISTS idx_diarization_jobs_meeting_id ON diarization_jobs(meeting_id);
CREATE INDEX IF NOT EXISTS idx_diarization_jobs_status ON diarization_jobs(status);
"""



def get_db_url():
    """Get database URL from environment."""
    return os.getenv("DB_CONNECTION_STRING") or os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")


async def apply_migration():
    """
    Apply the diarization migration to the database.
    
    Returns:
        bool: True if successful, False otherwise
    """
    db_url = get_db_url()
    
    if not db_url:
        logger.error("No database connection string found in environment")
        return False
    
    try:
        logger.info("Connecting to database...")
        conn = await asyncpg.connect(db_url)
        
        logger.info("Applying diarization migration...")
        
        # Execute migration SQL
        # Split into individual statements
        raw_statements = MIGRATION_SQL.split(';')
        statements = []
        
        for raw in raw_statements:
            # Remove comment lines and empty lines
            lines = [line for line in raw.split('\n') if line.strip() and not line.strip().startswith('--')]
            cleaned = '\n'.join(lines).strip()
            if cleaned:
                statements.append(cleaned)
        
        for i, statement in enumerate(statements):
            try:
                await conn.execute(statement)
                logger.debug(f"Executed statement {i + 1}/{len(statements)}")
            except asyncpg.exceptions.DuplicateColumnError:
                logger.info(f"Column already exists, skipping: {statement[:50]}...")
            except asyncpg.exceptions.DuplicateTableError:
                logger.info(f"Table already exists, skipping: {statement[:50]}...")
            except asyncpg.exceptions.DuplicateObjectError:
                logger.info(f"Object already exists, skipping: {statement[:50]}...")
            except Exception as e:
                logger.warning(f"Statement {i + 1} warning: {e}")
        
        # Verify migration
        result = await conn.fetchrow("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'transcript_segments' 
            AND column_name = 'speaker'
        """)
        
        if result:
            logger.info("✅ Migration verified: 'speaker' column exists")
        else:
            logger.warning("⚠️ Migration may have issues: 'speaker' column not found")
        
        await conn.close()
        
        logger.info("✅ Diarization migration completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return False


async def rollback_migration():
    """
    Rollback the diarization migration.
    
    WARNING: This will delete data! Only use in development.
    
    Returns:
        bool: True if successful, False otherwise
    """
    db_url = get_db_url()
    
    if not db_url:
        logger.error("No database connection string found in environment")
        return False
    
    try:
        logger.info("Connecting to database for rollback...")
        conn = await asyncpg.connect(db_url)
        
        rollback_sql = """
        -- Rollback diarization migration
        
        -- Drop tables
        DROP TABLE IF EXISTS diarization_jobs CASCADE;
        DROP TABLE IF EXISTS speaker_profiles CASCADE;
        DROP TABLE IF EXISTS meeting_speakers CASCADE;
        DROP TABLE IF EXISTS audio_chunks CASCADE;
        
        -- Remove columns from meetings (PostgreSQL doesn't support DROP COLUMN IF EXISTS directly)
        -- These will fail silently if columns don't exist
        """
        
        await conn.execute(rollback_sql)
        
        # Try to drop columns individually
        column_drops = [
            "ALTER TABLE meetings DROP COLUMN IF EXISTS diarization_status",
            "ALTER TABLE meetings DROP COLUMN IF EXISTS audio_recorded",
            "ALTER TABLE meetings DROP COLUMN IF EXISTS diarization_provider",
            "ALTER TABLE meetings DROP COLUMN IF EXISTS diarization_completed_at",
            "ALTER TABLE transcript_segments DROP COLUMN IF EXISTS speaker",
            "ALTER TABLE transcript_segments DROP COLUMN IF EXISTS speaker_confidence",
        ]
        
        for sql in column_drops:
            try:
                await conn.execute(sql)
            except Exception as e:
                logger.debug(f"Column drop skipped: {e}")
        
        await conn.close()
        
        logger.info("✅ Rollback completed")
        return True
        
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False


async def check_migration_status():
    """
    Check if the diarization migration has been applied.
    
    Returns:
        dict: Migration status details
    """
    db_url = get_db_url()
    
    if not db_url:
        return {"status": "error", "message": "No database connection string found"}
    
    try:
        conn = await asyncpg.connect(db_url)
        
        # Check for key indicators
        checks = {
            "speaker_column": False,
            "audio_chunks_table": False,
            "meeting_speakers_table": False,
            "diarization_jobs_table": False,
            "diarization_status_column": False
        }
        
        # Check speaker column in transcript_segments
        result = await conn.fetchrow("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'transcript_segments' AND column_name = 'speaker'
        """)
        checks["speaker_column"] = result is not None
        
        # Check tables
        for table in ["audio_chunks", "meeting_speakers", "diarization_jobs"]:
            result = await conn.fetchrow("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_name = $1
            """, table)
            checks[f"{table}_table"] = result is not None
        
        # Check diarization_status column
        result = await conn.fetchrow("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'meetings' AND column_name = 'diarization_status'
        """)
        checks["diarization_status_column"] = result is not None
        
        await conn.close()
        
        all_applied = all(checks.values())
        
        return {
            "status": "applied" if all_applied else "partial" if any(checks.values()) else "not_applied",
            "checks": checks
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "rollback":
            asyncio.run(rollback_migration())
        elif sys.argv[1] == "status":
            result = asyncio.run(check_migration_status())
            print(f"Migration status: {result}")
        else:
            print("Usage: python add_diarization_support.py [rollback|status]")
    else:
        asyncio.run(apply_migration())
