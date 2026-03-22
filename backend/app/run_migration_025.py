
import asyncio
import os
import asyncpg

async def run_migration():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    try:
        query = """
        ALTER TABLE full_transcripts
            ADD COLUMN IF NOT EXISTS metadata JSONB;
        COMMENT ON COLUMN full_transcripts.metadata IS 'Stores encryption metadata (wrappers, nonces) for the full transcript';
        """
        await conn.execute(query)
        print("✅ Migration 025 (metadata in full_transcripts) COMPLETED")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migration())
