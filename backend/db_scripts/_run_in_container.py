import asyncio
import asyncpg
import os
import ssl
import glob
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def _clean_url(db_url):
    parsed = urlparse(db_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("sslmode", None)
    params.pop("channel_binding", None)
    clean_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=clean_query))


async def run():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set"); return

    clean_url = _clean_url(db_url)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    conn = await asyncpg.connect(clean_url, ssl=ctx)
    print("Connected")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS migration_history (
            migration_id TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    migrations_dir = os.environ.get("MIGRATIONS_DIR", "migrations")
    files = glob.glob(os.path.join(migrations_dir, "*.sql"))

    def sort_key(f):
        m = re.match(r"(\d+)_", os.path.basename(f))
        return int(m.group(1)) if m else 999

    files.sort(key=sort_key)

    for f in files:
        mid = os.path.basename(f)
        exists = await conn.fetchval(
            "SELECT 1 FROM migration_history WHERE migration_id = $1", mid
        )
        if exists:
            print(f"  skip {mid}")
            continue
        print(f"  applying {mid} ...")
        sql = open(f).read()
        # Execute each statement separately so one failure doesn't block the rest
        failed = False
        for stmt in sql.split(";"):
            # Strip comment lines first, then check if anything remains
            lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
            stmt = "\n".join(lines).strip()
            if not stmt:
                continue
            try:
                await conn.execute(stmt)
            except Exception as e:
                print(f"    warn [{mid}] stmt failed (continuing): {e}")
                failed = True
        await conn.execute(
            "INSERT INTO migration_history (migration_id) VALUES ($1) ON CONFLICT DO NOTHING", mid
        )
        status = "WARN (partial)" if failed else "OK"
        print(f"  {status} {mid}")

    await conn.close()
    print("Done")


asyncio.run(run())
