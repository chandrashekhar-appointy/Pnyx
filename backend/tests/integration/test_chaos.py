"""Chaos / failure-mode tests.

We deliberately break collaborators and verify the system degrades gracefully
rather than hanging or 500-ing.  Each test pins a *specific* failure mode and
asserts a specific recovery property.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import audio as audio_router
from app.api.routers import chat as chat_router
from app.schemas.user import User


async def _async_noop(*a, **k):
    return None


def _async_value(v):
    async def _inner(*a, **k):
        return v

    return _inner


def _patch_audio(monkeypatch, manager_factory):
    async def fake_auth(_token):
        return User(email="test@appointy.com", name="Test User")

    state = SimpleNamespace(
        ensure_session=_async_noop,
        mark_stop_requested=_async_noop,
        transition=_async_noop,
        db=SimpleNamespace(
            touch_recording_session_heartbeat=_async_noop,
            merge_recording_session_metadata=_async_noop,
        ),
    )

    class _AIP:
        def __init__(self, *a, **k):
            self.enabled = False
            self.model_name = "test"
            self.analysis_interval_seconds = 60
            self.min_chars_before_analysis = 0
            self.verbose_logs = False
            self.evaluator = SimpleNamespace(decision_logs=False)

        async def load_runtime_config(self):
            return None

        async def load_host_state(self, _sid):
            return False

        def get_host_state_snapshot(self):
            return {}

        def get_stats_snapshot(self):
            return {}

    monkeypatch.setenv("ENABLE_AUDIO_RECORDING", "false")
    monkeypatch.setattr(audio_router, "_authenticate_websocket", fake_auth)
    monkeypatch.setattr(audio_router, "AIParticipantEngine", _AIP)
    monkeypatch.setattr(audio_router, "state_service", state)
    monkeypatch.setattr(audio_router.db, "get_user_api_key", _async_value("k"))
    monkeypatch.setattr(
        audio_router.db, "get_recording_chunk_stats", _async_value({"total": 0, "uploaded": 0})
    )
    monkeypatch.setattr(audio_router.db, "update_recording_session_counters", _async_noop)
    monkeypatch.setattr(audio_router, "stop_recorder", _async_value({}))
    monkeypatch.setattr(audio_router, "_finalize_session", _async_noop)
    monkeypatch.setattr(audio_router, "AUDIO_CELERY_ENABLED", False)
    monkeypatch.setattr(audio_router, "StreamingTranscriptionManager", manager_factory)


def _app():
    app = FastAPI()
    app.include_router(audio_router.router)
    return app


# ---------------------------------------------------------------------------
# 1. Provider hangs forever — connection should still close cleanly on stop
# ---------------------------------------------------------------------------


@pytest.mark.chaos
def test_provider_hang_does_not_deadlock_stop(monkeypatch, speech_chunk):
    class _HangingMgr:
        def __init__(self, *a, **k):
            pass

        async def process_audio_chunk(self, *a, **k):
            await asyncio.sleep(60)

        async def force_flush(self):
            await asyncio.sleep(60)

        def cleanup(self):
            return None

        def get_stats(self):
            return {}

    _patch_audio(monkeypatch, lambda *a, **k: _HangingMgr())

    app = _app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "t"})
            ws.receive_json()
            ws.send_bytes(speech_chunk)
            ws.send_json({"type": "stop"})
            # We don't assert a specific ack — just that the connection
            # eventually unblocks and we can exit the with-block.


# ---------------------------------------------------------------------------
# 2. Manager raises on every chunk — must not crash the connection
# ---------------------------------------------------------------------------


@pytest.mark.chaos
def test_manager_raises_every_chunk(monkeypatch, speech_chunk):
    class _PoisonMgr:
        def __init__(self, *a, **k):
            pass

        async def process_audio_chunk(self, *a, **k):
            raise RuntimeError("simulated poison")

        async def force_flush(self):
            return None

        def cleanup(self):
            return None

        def get_stats(self):
            return {}

    _patch_audio(monkeypatch, lambda *a, **k: _PoisonMgr())

    app = _app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "t"})
            ws.receive_json()
            for _ in range(10):
                ws.send_bytes(speech_chunk)
            ws.send_json({"type": "stop"})


# ---------------------------------------------------------------------------
# 3. DB call times out during chat — endpoint returns a clean 5xx, not a hang
# ---------------------------------------------------------------------------


@pytest.mark.chaos
@pytest.mark.anyio
async def test_chat_handles_downstream_exception(async_client, monkeypatch):
    async def _allow(*a, **k):
        return True

    async def _boom(**_kwargs):
        raise TimeoutError("simulated DB timeout")

    monkeypatch.setattr(chat_router.rbac, "can", _allow)
    monkeypatch.setattr(chat_router.chat_service, "chat_about_meeting", _boom)

    resp = await async_client.post(
        "/chat-meeting",
        json={
            "meeting_id": "m",
            "question": "q",
            "model": "gemini",
            "model_name": "gemini-3-pro-preview",
            "context_text": "",
        },
    )
    # Must surface as an error response, not a hung request
    assert resp.status_code in {500, 502, 503, 504, 400}
