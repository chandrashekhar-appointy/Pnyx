"""
Migration: Move transcript_segments rows to bucket-backed transcript_versions.

Run once after deploying the code that stops writing to transcript_segments.

Usage (inside the backend Docker container or with DATABASE_URL set):
    python -m app.migrate_segments_to_bucket
    # or
    python app/migrate_segments_to_bucket.py
"""

import asyncio
import hashlib
import json
import logging
import os
from collections import defaultdict
from datetime import datetime

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_segments_to_bucket")


async def migrate(conn: asyncpg.Connection) -> None:
    # Load all remaining transcript_segments rows grouped by meeting_id
    rows = await conn.fetch(
        """
        SELECT ts.meeting_id,
               ts.transcript,
               ts.timestamp,
               ts.audio_start_time,
               ts.audio_end_time,
               ts.source,
               ts.speaker,
               ts.speaker_confidence,
               ts.alignment_state
        FROM transcript_segments ts
        ORDER BY ts.meeting_id, ts.id ASC
        """
    )

    if not rows:
        logger.info("transcript_segments is empty — nothing to migrate.")
        return

    # Group by meeting_id
    by_meeting: dict[str, list] = defaultdict(list)
    for row in rows:
        by_meeting[row["meeting_id"]].append(dict(row))

    logger.info("Found %d meeting(s) with segments to migrate.", len(by_meeting))

    # Import storage service (works when running from backend root)
    try:
        from app.services.document_storage import DocumentStorageService
    except ImportError:
        from services.document_storage import DocumentStorageService

    migrated_meetings = 0
    migrated_rows = 0

    for meeting_id, segs in by_meeting.items():
        # Check if a transcript_versions row already covers this data (skip if already migrated)
        existing = await conn.fetchval(
            "SELECT 1 FROM transcript_versions WHERE meeting_id = $1 LIMIT 1",
            meeting_id,
        )

        # Build normalized segment list for the bucket
        segments = [
            {
                "text": s["transcript"],
                "timestamp": s["timestamp"] or "",
                "start": s["audio_start_time"],
                "end": s["audio_end_time"],
                "speaker": s["speaker"],
                "speaker_confidence": s["speaker_confidence"],
                "source": s["source"] or "live",
                "alignment_state": s["alignment_state"],
            }
            for s in segs
        ]

        max_end = max(
            (float(s["end"] or 0) for s in segments if s["end"] is not None),
            default=0,
        )

        # Determine next version_num
        version_num = await conn.fetchval(
            "SELECT COALESCE(MAX(version_num), 0) + 1 FROM transcript_versions WHERE meeting_id = $1",
            meeting_id,
        )

        # Upload to bucket
        try:
            storage_meta = await DocumentStorageService.save_transcript_version(
                meeting_id=meeting_id,
                version_num=version_num,
                source="live",
                content=segments,
                is_authoritative=(not existing),  # authoritative only if no version yet
                created_by="migration",
                alignment_config={"total_duration_seconds": max_end},
                confidence_metrics={},
            )
        except Exception as exc:
            logger.error("Failed to upload segments for meeting %s: %s", meeting_id, exc)
            continue

        # Insert metadata row into transcript_versions
        try:
            await conn.execute(
                """
                INSERT INTO transcript_versions (
                    meeting_id, version_num, source,
                    is_authoritative, created_by, alignment_config, confidence_metrics,
                    content_object_path, content_sha256, content_byte_size
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (meeting_id, version_num) DO NOTHING
                """,
                meeting_id,
                version_num,
                "live",
                not existing,
                "migration",
                json.dumps({"total_duration_seconds": max_end}),
                json.dumps({}),
                storage_meta["path"],
                storage_meta["sha256"],
                storage_meta["byte_size"],
            )
        except Exception as exc:
            logger.error(
                "Failed to insert transcript_versions row for meeting %s: %s", meeting_id, exc
            )
            continue

        # Delete migrated rows from transcript_segments
        deleted = await conn.execute(
            "DELETE FROM transcript_segments WHERE meeting_id = $1",
            meeting_id,
        )
        logger.info(
            "  ✅ meeting=%s  segments=%d  version=v%d  bucket=%s  deleted=%s",
            meeting_id,
            len(segs),
            version_num,
            storage_meta["path"],
            deleted,
        )
        migrated_meetings += 1
        migrated_rows += len(segs)

    logger.info(
        "Migration complete: %d meetings, %d segment rows moved to bucket.",
        migrated_meetings,
        migrated_rows,
    )

    # Verify transcript_segments is now empty
    remaining = await conn.fetchval("SELECT COUNT(*) FROM transcript_segments")
    if remaining == 0:
        logger.info("✅ transcript_segments table is now empty.")
    else:
        logger.warning(
            "⚠️  %d rows still remain in transcript_segments (check errors above).",
            remaining,
        )


async def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    conn = await asyncpg.connect(db_url)
    try:
        await migrate(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
