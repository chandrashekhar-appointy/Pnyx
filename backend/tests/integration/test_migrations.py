"""DB migration tests.

The repo uses raw ``.sql`` files in ``backend/app/migrations/`` rather than
Alembic.  These tests:

  * apply every file in lexical order against an ephemeral Postgres
  * assert key tables exist post-migration
  * detect destructive changes (e.g. a new file that ``DROP``s a column)

They are gated by ``INTEGRATION_DB=1`` so the default suite stays Docker-less.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.database import _migration_files, apply_migrations

EXPECTED_TABLES = {
    "users",
    "meetings",
    "transcript_segments",
    "recording_sessions",
    "recording_chunks",
    "user_credits",
    "credit_ledger",
    "shared_meeting_notes",
    "analytics_events",
}


@pytest.mark.integration
@pytest.mark.anyio
async def test_migrations_apply_cleanly(real_db):
    """The session-scoped real_db fixture has already applied all migrations.
    Re-applying is non-destructive — verify the second run does not error."""
    await apply_migrations(real_db)


@pytest.mark.integration
@pytest.mark.anyio
async def test_migrations_create_expected_tables(real_db):
    from tests.fixtures.database import fetch

    rows = await fetch(
        real_db,
        "SELECT tablename FROM pg_tables WHERE schemaname='public'",
    )
    names = {r["tablename"] for r in rows}
    missing = EXPECTED_TABLES - names
    assert not missing, f"Migrations did not create: {missing}"


def test_no_migration_drops_column_without_marker():
    """Any migration that issues an ``ALTER TABLE ... DROP COLUMN`` must
    include a marker comment (``-- destructive: ack``) on the same file.

    Catches risky destructive changes that slip past review."""

    drop_re = re.compile(
        r"\bALTER\s+TABLE\s+\S+\s+DROP\s+COLUMN\b", re.IGNORECASE
    )
    marker = "destructive: ack"

    offenders: list[str] = []
    for path in _migration_files():
        text = path.read_text(encoding="utf-8")
        if drop_re.search(text) and marker not in text.lower():
            offenders.append(path.name)
    assert not offenders, (
        "Destructive migrations missing `-- destructive: ack` marker: "
        + ", ".join(offenders)
    )
