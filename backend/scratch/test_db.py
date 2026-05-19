import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def test_connection():
    db_url = os.getenv("DATABASE_URL")
    print(f"Connecting to: {db_url}")
    try:
        conn = await asyncpg.connect(db_url)
        print("✅ Connection successful!")
        version = await conn.fetchval("SELECT version()")
        print(f"PostgreSQL version: {version}")
        await conn.close()
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
