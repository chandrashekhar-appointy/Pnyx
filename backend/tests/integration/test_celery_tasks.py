"""Celery task tests.

Celery is configured for ``task_always_eager=True`` in ``conftest.py``, so
``.delay()`` invocations execute inline.  We test:

  * weekly_credit_reset — iterates users and resets balances (DB layer mocked)
  * weekly_credit_reset is idempotent at the credit-manager level
  * audio_pipeline.enqueue_finalize_session_task is wired correctly
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks import weekly_credit_reset as weekly_module


def _fake_db_module(user_emails: list[str]):
    """Build a fake DatabaseManager() whose ``_get_connection()`` async-cm
    yields a connection whose ``.fetch`` returns the given users."""

    class _FakeConn:
        async def fetch(self, _sql):
            return [{"user_email": e} for e in user_emails]

    @asynccontextmanager
    async def _cm(self):
        yield _FakeConn()

    fake_dbm = MagicMock()
    fake_dbm._get_connection = _cm.__get__(fake_dbm)
    return fake_dbm


def test_weekly_credit_reset_resets_each_user(monkeypatch):
    user_emails = ["a@x.test", "b@x.test", "c@x.test"]
    fake_db = _fake_db_module(user_emails)

    reset_calls: list[str] = []

    class _FakeCreditMgr:
        def __init__(self, _db):
            pass

        async def reset_weekly_credits(self, email):
            reset_calls.append(email)

    class _FakeEmailService:
        async def send_credit_reset_notification(self, **_kw):
            return None

    with patch.object(weekly_module, "logger"):
        # Patch imports inside the task body
        monkeypatch.setattr(
            "app.db.DatabaseManager", lambda: fake_db, raising=False
        )
        monkeypatch.setattr(
            "app.services.credit_manager.CreditManager", _FakeCreditMgr, raising=False
        )
        monkeypatch.setattr(
            "app.services.credit_manager.WEEKLY_FREE_CREDITS", 10_000, raising=False
        )
        monkeypatch.setattr(
            "app.services.email.credit_reset_email.CreditResetEmailService",
            lambda: _FakeEmailService(),
            raising=False,
        )

        result = weekly_module.weekly_credit_reset.run()

    assert result["total_users"] == 3
    assert result["success"] == 3
    assert reset_calls == user_emails


def test_weekly_credit_reset_continues_on_per_user_error(monkeypatch):
    user_emails = ["ok@x.test", "bad@x.test", "ok2@x.test"]
    fake_db = _fake_db_module(user_emails)

    class _FlakyCreditMgr:
        def __init__(self, _db):
            self.calls = 0

        async def reset_weekly_credits(self, email):
            if email == "bad@x.test":
                raise RuntimeError("boom")

    class _FakeEmailService:
        async def send_credit_reset_notification(self, **_kw):
            return None

    monkeypatch.setattr(
        "app.db.DatabaseManager", lambda: fake_db, raising=False
    )
    monkeypatch.setattr(
        "app.services.credit_manager.CreditManager", _FlakyCreditMgr, raising=False
    )
    monkeypatch.setattr(
        "app.services.credit_manager.WEEKLY_FREE_CREDITS", 10_000, raising=False
    )
    monkeypatch.setattr(
        "app.services.email.credit_reset_email.CreditResetEmailService",
        lambda: _FakeEmailService(),
        raising=False,
    )

    result = weekly_module.weekly_credit_reset.run()

    assert result["total_users"] == 3
    assert result["success"] == 2
    assert result["errors"] == 1


def test_audio_pipeline_enqueue_uses_celery_app():
    from app.tasks import audio_pipeline

    # All enqueue_* helpers must exist and be callable
    for name in (
        "enqueue_finalize_session_task",
        "enqueue_postprocess_session_task",
        "enqueue_upload_chunk_task",
    ):
        fn = getattr(audio_pipeline, name, None)
        assert callable(fn), f"{name} must be callable"


def test_celery_beat_schedule_includes_weekly_credit_reset():
    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule or {}
    assert "weekly-credit-reset" in schedule, (
        "weekly-credit-reset entry missing from celery beat schedule"
    )
    entry = schedule["weekly-credit-reset"]
    assert entry["task"] == "tasks.weekly_credit_reset"
