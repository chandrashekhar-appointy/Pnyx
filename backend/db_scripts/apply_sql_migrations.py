import os
import asyncio
import asyncpg
import glob
import re
import ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dotenv import load_dotenv

load_dotenv()


def _strip_unsupported_params(db_url: str):
    """Remove sslmode/channel_binding from URL; return (clean_url, ssl_ctx)."""
    parsed = urlparse(db_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    sslmode = params.pop("sslmode", ["prefer"])[0]
    params.pop("channel_binding", None)

    clean_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=clean_query))

    ssl_ctx: bool | ssl.SSLContext = False
    if sslmode in ("require", "verify-ca", "verify-full"):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    elif sslmode in ("allow", "prefer"):
        ssl_ctx = False

    return clean_url, ssl_ctx


async def apply_migrations():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set")
        return

    clean_url, ssl_ctx = _strip_unsupported_params(db_url)

    print(f"🚀 Connecting to database to apply migrations...")
    try:
        conn = await asyncpg.connect(clean_url, ssl=ssl_ctx)
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
