import asyncio
import asyncpg
import os


async def clear_data():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    try:
        conn = await asyncpg.connect(db_url)
        await conn.execute("DELETE FROM analytics_events;")
        print("Successfully deleted all analytics_events data.")
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(clear_data())
