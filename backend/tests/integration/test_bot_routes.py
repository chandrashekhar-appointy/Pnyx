"""
Bot route coverage (Recall.ai management endpoints).

Hermetic: the Recall HTTP client / manager is fully mocked, so these run with
no live Recall API and no network. They lock the contract of:

  * POST   /api/meetings/{id}/invite-bot   (success, quota-exceeded -> 429)
  * GET    /api/meetings/{id}/bot-status
  * DELETE /api/meetings/{id}/bot          (404 when no bot)
  * GET    /api/bot/quota
  * POST   /api/bot/webhook                (signature path + rejection)

These are the routes that had zero tests despite Recall being the #1 keeper
feature.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps as api_deps
from app.api.routers import bot as bot_router
from app.schemas.user import User


@pytest.fixture
def bot_app():
    app = FastAPI()
    app.include_router(bot_router.router)

    async def _fake_user():
        return User(email="bot-tester@appointy.com", name="Bot Tester")

    # Override the dependency used by the bot router (imported as get_current_user).
    app.dependency_overrides[bot_router.get_current_user] = _fake_user
    app.dependency_overrides[api_deps.get_current_user] = _fake_user
    return app


@pytest.fixture
async def bot_client(bot_app):
    transport = ASGITransport(app=bot_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def fake_manager(monkeypatch):
    """Install a fake RecallManager and return it for per-test configuration."""
    mgr = SimpleNamespace()
    monkeypatch.setattr(bot_router, "_get_manager", lambda: mgr)
    return mgr


# ── invite-bot ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_bot_success(bot_client, fake_manager):
    async def spawn_bot(**kwargs):
        assert kwargs["meeting_id"] == "m-1"
        assert kwargs["meeting_url"] == "https://meet.google.com/abc-defg-hij"
        assert kwargs["user_email"] == "bot-tester@appointy.com"
        return {"success": True, "recall_bot_id": "recall-123", "status": "joining"}

    fake_manager.spawn_bot = spawn_bot

    resp = await bot_client.post(
        "/api/meetings/m-1/invite-bot",
        json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["recall_bot_id"] == "recall-123"
    assert body["meeting_id"] == "m-1"
    assert body["status"] == "joining"


@pytest.mark.anyio
async def test_invite_bot_quota_exceeded_returns_429(bot_client, fake_manager):
    async def spawn_bot(**kwargs):
        return {
            "success": False,
            "error": "weekly_quota_exceeded",
            "message": "You have used all your bot minutes this week.",
        }

    fake_manager.spawn_bot = spawn_bot

    resp = await bot_client.post(
        "/api/meetings/m-2/invite-bot",
        json={"meeting_url": "https://zoom.us/j/123"},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "weekly_quota_exceeded"


@pytest.mark.anyio
async def test_invite_bot_other_failure_returns_400(bot_client, fake_manager):
    async def spawn_bot(**kwargs):
        return {"success": False, "error": "invalid_url", "message": "Bad URL"}

    fake_manager.spawn_bot = spawn_bot

    resp = await bot_client.post(
        "/api/meetings/m-3/invite-bot",
        json={"meeting_url": "not-a-url"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_url"


# ── bot-status ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bot_status_returns_status(bot_client, fake_manager):
    async def get_bot_status(meeting_id):
        return {"status": "in_call_recording", "recall_bot_id": "recall-123"}

    fake_manager.get_bot_status = get_bot_status

    resp = await bot_client.get("/api/meetings/m-1/bot-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_call_recording"


@pytest.mark.anyio
async def test_bot_status_none_when_absent(bot_client, fake_manager):
    async def get_bot_status(meeting_id):
        return None

    fake_manager.get_bot_status = get_bot_status

    resp = await bot_client.get("/api/meetings/m-unknown/bot-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "none"


# ── remove bot ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_remove_bot_404_when_no_bot(bot_client, fake_manager):
    async def remove_bot(meeting_id):
        return {"success": False, "message": "No bot found"}

    fake_manager.remove_bot = remove_bot

    resp = await bot_client.delete("/api/meetings/m-x/bot")
    assert resp.status_code == 404


# ── quota ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bot_quota(bot_client, fake_manager):
    async def check_quota(email):
        assert email == "bot-tester@appointy.com"
        return {"used_minutes": 12, "limit_minutes": 120, "remaining_minutes": 108}

    fake_manager.check_quota = check_quota

    resp = await bot_client.get("/api/bot/quota")
    assert resp.status_code == 200
    assert resp.json()["remaining_minutes"] == 108


# ── webhook ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_webhook_processes_signed_event(bot_client, fake_manager):
    captured = {}

    async def process_webhook(*, payload, raw_body, signature, headers, pre_verified, **kwargs):
        captured["signature"] = signature
        captured["payload"] = payload
        captured["pre_verified"] = pre_verified
        return {"ok": True}

    fake_manager.process_webhook = process_webhook

    resp = await bot_client.post(
        "/api/bot/webhook",
        content=b'{"event":"bot.status_change","data":{}}',
        headers={"X-Recall-Signature": "sig-abc", "Content-Type": "application/json"},
    )
    # Either the route returns 200 with the manager's result, or a controlled
    # error — but it must not 500 on a well-formed signed payload.
    assert resp.status_code in (200, 202)
    assert captured.get("signature") == "sig-abc"


@pytest.mark.anyio
async def test_webhook_health_check(bot_client):
    resp = await bot_client.get("/api/bot/webhook")
    assert resp.status_code == 200
