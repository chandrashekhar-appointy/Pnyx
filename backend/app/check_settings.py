import asyncio
import os
from db.manager import DatabaseManager
async def main():
    db = DatabaseManager()
    await db.init_pool(os.getenv('DATABASE_URL'))
    async with db._get_connection() as conn:
        rows = await conn.fetch('SELECT id FROM settings')
        print(f'IDs in settings: {[r[0] for r in rows]}')
    await db.close_pool()
asyncio.run(main())
