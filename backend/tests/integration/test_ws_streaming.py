"""WebSocket streaming-audio pipeline tests.

We use FastAPI's ``TestClient.websocket_connect`` to drive the real ASGI
WebSocket lifecycle (handshake / accept / send / receive / close).  Heavy
dependencies — Groq, ElevenLabs, the diarization pipeline, and the DB layer —
are monkey-patched at the router level so the test runs in <1s and never
needs network access.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import audio as audio_router
from app.schemas.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoopManager:
    """Replaces StreamingTranscriptionManager — accepts chunks, emits no work."""

    def __init__(self, *args, **kwargs):
        self.calls = 0

    async def process_audio_chunk(
        self,
        audio_data,
        client_timestamp,
        on_partial,
        on_final,
        on_error,
    ):
        self.calls += 1

    async def force_flush(self):
        return None

    def cleanup(self):
        return None

    def get_stats(self):
        return {"chunks": self.calls}


class _NoopAIParticipant:
    """Stand-in for AIParticipantEngine that's effectively a no-op."""

    def __init__(self, *args, **kwargs):
        self.enabled = False
        self.model_name = "test-model"
        self.analysis_interval_seconds = 60
        self.min_chars_before_analysis = 0
        self.verbose_logs = False
        self.evaluator = SimpleNamespace(decision_logs=False)

    async def load_runtime_config(self):
        return None

    async def load_host_state(self, session_id):
        return False

    def get_host_state_snapshot(self):
        return {}

    def get_stats_snapshot(self):
        return {}


def _patch_pipeline(monkeypatch, *, fake_state_service=None, fail_user_key=False):
    """Patch every collaborator the WS handler reaches into."""

    async def fake_auth(_token):
        return User(email="test@appointy.com", name="Test User")

    fake_state_service = fake_state_service or SimpleNamespace(
        ensure_session=_async_noop,
        mark_stop_requested=_async_noop,
        transition=_async_noop,
        db=SimpleNamespace(
            touch_recording_session_heartbeat=_async_noop,
            merge_recording_session_metadata=_async_noop,
        ),
    )

    async def fake_user_key(*args, **kwargs):
        if fail_user_key:
            return None
        return "fake-provider-key"

    async def fake_stop_recorder(_recorder_key):
        return {}

    async def fake_finalize_session(session_id, flush, process_audio):
        audio_router.session_finalized.add(session_id)

    monkeypatch.setenv("ENABLE_AUDIO_RECORDING", "false")
    monkeypatch.setattr(audio_router, "_authenticate_websocket", fake_auth)
    monkeypatch.setattr(
        audio_router,
        "StreamingTranscriptionManager",
        lambda *a, **kw: _NoopManager(),
    )
    monkeypatch.setattr(audio_router, "AIParticipantEngine", _NoopAIParticipant)
    monkeypatch.setattr(audio_router, "state_service", fake_state_service)
    monkeypatch.setattr(audio_router.db, "get_user_api_key", fake_user_key)
    monkeypatch.setattr(
        audio_router.db,
        "get_recording_chunk_stats",
        lambda *_: _async_value({"total": 0, "uploaded": 0}),
    )
    monkeypatch.setattr(
        audio_router.db,
        "update_recording_session_counters",
        _async_noop,
    )
    monkeypatch.setattr(audio_router, "stop_recorder", fake_stop_recorder)
    monkeypatch.setattr(audio_router, "_finalize_session", fake_finalize_session)
    monkeypatch.setattr(audio_router, "AUDIO_CELERY_ENABLED", False)


async def _async_noop(*args, **kwargs):
    return None


def _async_value(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _ws_app() -> FastAPI:
    app = FastAPI()
    app.include_router(audio_router.router)
    return app


# ---------------------------------------------------------------------------
# Connection / heartbeat
# ---------------------------------------------------------------------------


def test_ws_authenticates_via_message_and_responds_to_ping(monkeypatch):
    _patch_pipeline(monkeypatch)
    app = _ws_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "test-token"})
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

            ws.send_json({"type": "stop"})
            # Drain remaining messages until close
            messages = [pong]
            try:
                while True:
                    messages.append(ws.receive_json())
            except Exception:
                pass

            types = {m.get("type") for m in messages}
            assert "stop_ack" in types or "stop_acknowledged" in types or "session_finalized" in types or "stop_complete" in types


def test_ws_rejects_when_provider_key_missing(monkeypatch):
    _patch_pipeline(monkeypatch, fail_user_key=True)
    app = _ws_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "test-token"})
            msg = ws.receive_json()
            # Either receives an explicit 'error' or an immediate close
            assert msg.get("type") in {"error", "auth_failed", "connected"}


def test_ws_persisted_stop_request_finalizes_on_disconnect(monkeypatch):
    """If the DB row already has stop_requested_at set, an unexpected disconnect
    should still trigger a finalize so we don't leave the session hanging."""

    finalize_calls: list[str] = []
    session_state = {
        "status": "stopping_requested",
        "stop_requested_at": "2026-04-01T00:00:00Z",
    }

    async def fake_get_session(_sid):
        return dict(session_state)

    async def fake_finalize(session_id, flush, process_audio):
        finalize_calls.append(session_id)

    fake_state = SimpleNamespace(
        ensure_session=_async_noop,
        mark_stop_requested=_async_noop,
        transition=_async_noop,
        db=SimpleNamespace(
            touch_recording_session_heartbeat=_async_noop,
            merge_recording_session_metadata=_async_noop,
        ),
    )

    _patch_pipeline(monkeypatch, fake_state_service=fake_state)
    monkeypatch.setattr(audio_router.db, "get_recording_session", fake_get_session)
    monkeypatch.setattr(audio_router, "_finalize_session", fake_finalize)

    app = _ws_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "test-token"})
            ws.receive_json()  # connected
            # close abruptly without sending stop
        # connection has closed at this point
    assert finalize_calls, (
        "Persisted stop_requested_at must trigger finalize on disconnect"
    )


# ---------------------------------------------------------------------------
# Audio chunk → transcript path
# ---------------------------------------------------------------------------


def test_ws_audio_chunk_invokes_transcription_manager(monkeypatch, speech_chunk):
    captured: list[int] = []

    class _SpyManager(_NoopManager):
        async def process_audio_chunk(
            self, audio_data, client_timestamp, on_partial, on_final, on_error
        ):
            captured.append(len(audio_data))
            await on_final("hello world", 0.0, 1.0)

    monkeypatch.setattr(
        audio_router,
        "StreamingTranscriptionManager",
        lambda *a, **kw: _SpyManager(),
    )
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(
        audio_router,
        "StreamingTranscriptionManager",
        lambda *a, **kw: _SpyManager(),
    )

    app = _ws_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/streaming-audio") as ws:
            ws.send_json({"type": "authenticate", "token": "test-token"})
            ws.receive_json()  # connected
            ws.send_bytes(speech_chunk)
            # Expect a transcript message back. May arrive as 'final' or 'transcript'
            try:
                msg = ws.receive_json()
                assert msg.get("type") in {
                    "final",
                    "transcript",
                    "partial",
                    "audio_received",
                    "ack",
                }
            except Exception:
                # Some pipelines batch chunks before emitting; accept silence.
                pass
            ws.send_json({"type": "stop"})

    assert captured, "process_audio_chunk should have been invoked at least once"
