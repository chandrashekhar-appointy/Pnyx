import asyncio
import json
import logging
import os
from pathlib import Path

import asyncpg

from services.document_storage import DocumentStorageService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_large_content_to_bucket")


ROOT = Path(__file__).resolve().parent
MIGRATION_SQL_PATH = ROOT / "migrations" / "018_bucket_document_storage.sql"


async def apply_migration_sql(conn: asyncpg.Connection) -> None:
    sql = MIGRATION_SQL_PATH.read_text()
    statements = [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
    for stmt in statements:
        await conn.execute(stmt)


async def backfill_full_transcripts(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT meeting_id, transcript_text, model, model_name, chunk_size, overlap
        FROM full_transcripts
        WHERE transcript_text IS NOT NULL
          AND COALESCE(transcript_object_path, '') = ''
        """
    )
    updated = 0
    for row in rows:
        storage_meta = await DocumentStorageService.save_full_transcript(
            meeting_id=row["meeting_id"],
            transcript_text=row["transcript_text"],
            model=row["model"],
            model_name=row["model_name"],
            chunk_size=row["chunk_size"] or 0,
            overlap=row["overlap"] or 0,
        )
        await conn.execute(
            """
            UPDATE full_transcripts
            SET transcript_object_path = $2,
                transcript_sha256 = $3,
                transcript_byte_size = $4,
                transcript_preview = $5,
                transcript_text = NULL
            WHERE meeting_id = $1
            """,
            row["meeting_id"],
            storage_meta["path"],
            storage_meta["sha256"],
            storage_meta["byte_size"],
            storage_meta["preview"],
        )
        updated += 1
    return updated


async def backfill_transcript_versions(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT meeting_id, version_num, source, content_json, is_authoritative,
               created_by, alignment_config, confidence_metrics
        FROM transcript_versions
        WHERE content_json IS NOT NULL
          AND COALESCE(content_object_path, '') = ''
        """
    )
    updated = 0
    for row in rows:
        content = row["content_json"]
        if isinstance(content, str):
            content = json.loads(content)
        alignment_config = row["alignment_config"]
        if isinstance(alignment_config, str):
            alignment_config = json.loads(alignment_config)
        confidence_metrics = row["confidence_metrics"]
        if isinstance(confidence_metrics, str):
            confidence_metrics = json.loads(confidence_metrics)

        storage_meta = await DocumentStorageService.save_transcript_version(
            meeting_id=row["meeting_id"],
            version_num=row["version_num"],
            source=row["source"],
            content=content,
            is_authoritative=bool(row["is_authoritative"]),
            created_by=row["created_by"] or "system",
            alignment_config=alignment_config,
            confidence_metrics=confidence_metrics,
        )
        await conn.execute(
            """
            UPDATE transcript_versions
            SET content_object_path = $3,
                content_sha256 = $4,
                content_byte_size = $5,
                content_json = NULL
            WHERE meeting_id = $1 AND version_num = $2
            """,
            row["meeting_id"],
            row["version_num"],
            storage_meta["path"],
            storage_meta["sha256"],
            storage_meta["byte_size"],
        )
        updated += 1
    return updated


async def backfill_summary_results(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT meeting_id, result
        FROM summary_processes
        WHERE result IS NOT NULL
          AND COALESCE(result_object_path, '') = ''
        """
    )
    updated = 0
    for row in rows:
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            continue
        storage_meta = await DocumentStorageService.save_summary_result(
            row["meeting_id"], result
        )
        await conn.execute(
            """
            UPDATE summary_processes
            SET result_object_path = $2,
                result_sha256 = $3,
                result_byte_size = $4,
                result_preview = $5,
                result = NULL
            WHERE meeting_id = $1
            """,
            row["meeting_id"],
            storage_meta["path"],
            storage_meta["sha256"],
            storage_meta["byte_size"],
            storage_meta["preview"],
        )
        updated += 1
    return updated


async def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")

    conn = await asyncpg.connect(db_url)
    try:
        await apply_migration_sql(conn)
        full_count = await backfill_full_transcripts(conn)
        version_count = await backfill_transcript_versions(conn)
        summary_count = await backfill_summary_results(conn)
        logger.info(
            "Backfill complete: full_transcripts=%s transcript_versions=%s summary_processes=%s",
            full_count,
            version_count,
            summary_count,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
