"""Ephemeral PostgreSQL helper for integration tests.

Two modes:

  * **Lightweight (default).** Tests use the existing FastAPI app with monkey-
    patched DB functions — no real DB, runs in plain `pytest`.
  * **Real DB.** When the env var ``INTEGRATION_DB=1`` is set, this module
    spins up an ephemeral Postgres via `testcontainers` and applies every SQL
    file under ``app/migrations/`` in lexical order.  Tests opting in request
    the ``real_db`` fixture.

CI is expected to set ``INTEGRATION_DB=1`` for nightly / scheduled runs but
not for per-PR runs (they should stay fast).
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import AsyncIterator

import asyncpg

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = BACKEND_ROOT / "app" / "migrations"


_MIGRATION_FILE_RE = re.compile(r"^(\d{3,4})_.+\.sql$")


def _migration_files() -> list[Path]:
    files: list[Path] = []
    for entry in MIGRATIONS_DIR.iterdir():
        if not entry.is_file():
            continue
        if _MIGRATION_FILE_RE.match(entry.name):
            files.append(entry)
    files.sort(key=lambda p: p.name)
    return files


async def apply_migrations(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for path in _migration_files():
            sql = path.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Migration {path.name} failed: {exc}") from exc
    finally:
        await conn.close()


def integration_db_enabled() -> bool:
    return os.getenv("INTEGRATION_DB", "0") in {"1", "true", "yes"}


# Lazy import so unit tests don't pay the testcontainers import cost
def _start_postgres_container():  # pragma: no cover - import at call time
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("pgvector/pgvector:pg15")
    container.start()
    return container


class EphemeralPostgres:
    """Async helper that wraps a running testcontainers Postgres."""

    def __init__(self) -> None:
        self._container = None
        self._dsn: str | None = None

    async def start(self) -> str:
        loop = asyncio.get_running_loop()
        self._container = await loop.run_in_executor(None, _start_postgres_container)
        url = self._container.get_connection_url()
        # testcontainers returns SQLAlchemy-style URL; convert for asyncpg
        self._dsn = url.replace("postgresql+psycopg2://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        await apply_migrations(self._dsn)
        return self._dsn

    async def stop(self) -> None:
        if self._container is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._container.stop)
        self._container = None
        self._dsn = None

    @property
    def dsn(self) -> str:
        assert self._dsn is not None, "EphemeralPostgres not started"
        return self._dsn


async def truncate_all(dsn: str, exclude: set[str] | None = None) -> None:
    """Truncate every public-schema table — call between tests for isolation."""
    exclude = exclude or set()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        names = [r["tablename"] for r in rows if r["tablename"] not in exclude]
        if not names:
            return
        joined = ", ".join(f'"{n}"' for n in names)
        await conn.execute(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def insert_user(dsn: str, email: str, name: str = "Test User") -> None:
    """Convenience seeder used by integration tests that need a stable user row."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (email, name, created_at, updated_at)
            VALUES ($1, $2, NOW(), NOW())
            ON CONFLICT (email) DO NOTHING
            """,
            email,
            name,
        )
    finally:
        await conn.close()


async def fetchval(dsn: str, query: str, *args) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


async def fetch(dsn: str, query: str, *args) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]
    finally:
        await conn.close()
