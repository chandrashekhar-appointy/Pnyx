import os
import asyncio
import asyncpg
import glob
import re
from dotenv import load_dotenv

load_dotenv()

async def apply_migrations():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set")
        return

    print(f"🚀 Connecting to database to apply migrations...")
    try:
        conn = await asyncpg.connect(db_url)
        print("✅ Connected!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return

    # Create migrations table to track applied migrations
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS migration_history (
            migration_id TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Get list of .sql files in backend/app/migrations
    migrations_dir = "app/migrations"
    sql_files = glob.glob(os.path.join(migrations_dir, "*.sql"))
    
    # Sort files by numerical prefix
    def get_prefix(filename):
        match = re.match(r"(\d+)_", os.path.basename(filename))
        return int(match.group(1)) if match else 999

    sql_files.sort(key=get_prefix)

    for sql_file in sql_files:
        migration_id = os.path.basename(sql_file)
        
        # Check if already applied
        exists = await conn.fetchval("SELECT 1 FROM migration_history WHERE migration_id = $1", migration_id)
        if exists:
            print(f"⏩ Skipping {migration_id} (already applied)")
            continue

        print(f"🔄 Applying {migration_id}...")
        try:
            with open(sql_file, "r") as f:
                sql_content = f.read()
            
            # Split by semicolon to execute separate statements if needed, 
            # but asyncpg can handle multiple statements if they don't return values.
            # However, some might have DO blocks or complex stuff.
            # Let's try executing the whole block first.
            await conn.execute(sql_content)
            
            await conn.execute("INSERT INTO migration_history (migration_id) VALUES ($1)", migration_id)
            print(f"✅ Applied {migration_id}")
        except Exception as e:
            print(f"❌ Failed to apply {migration_id}: {e}")
            # Optional: break if a migration fails to avoid inconsistent state
            break

    await conn.close()
    print("🏁 Migration process finished.")

if __name__ == "__main__":
    asyncio.run(apply_migrations())
