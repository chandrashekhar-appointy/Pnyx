import asyncio
import os
import sys

# Add app to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from db.manager import DatabaseManager

async def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set")
        return
        
    await DatabaseManager.init_pool(db_url)
    db = DatabaseManager()
    
    meeting_id = '148ec729-3199-4709-b9ac-3e22b51dd0a2'
    process = await db.get_transcript_data(meeting_id)
    print("Process Data:", process)
    
    await DatabaseManager.close_pool()

if __name__ == "__main__":
    asyncio.run(main())
