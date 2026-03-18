import json
from types import SimpleNamespace
from datetime import datetime

import pytest
from fastapi.websockets import WebSocketDisconnect

from app.api.routers import audio as audio_router
from app.api.routers import transcripts as transcripts_router
from app.tasks import audio_pipeline as audio_pipeline_tasks


@pytest.mark.anyio
async def test_websocket_streaming_connects(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False
            self._messages = [
                {"text": json.dumps({"type": "ping"})},
                {"text": json.dumps({"type": "stop"})},
            ]

        async def accept(self):
            return None

        async def close(self, code=None, reason=None):
            self.closed = True

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive(self):
            if not self._messages:
                raise WebSocketDisconnect()
            return self._messages.pop(0)

    async def fake_auth(_token):
        from app.schemas.user import User

        return User(email="test@appointy.com", name="Test User")

    async def fake_ensure_session(**kwargs):
        return None

    async def fake_transition(*args, **kwargs):
        return None

    async def fake_touch_heartbeat(_session_id):
        return None

    async def fake_merge_metadata(*args, **kwargs):
        return None

    async def fake_get_chunk_stats(_session_id):
        return {"total": 0, "uploaded": 0}

    async def fake_update_counters(**kwargs):
        return None

    async def fake_stop_recorder(_recorder_key):
        return {}

    class _FakePostService:
        async def finalize_recording(self, *args, **kwargs):
            return {"status": "completed"}

    fake_state_service = SimpleNamespace(
        ensure_session=fake_ensure_session,
        mark_stop_requested=fake_ensure_session,
        transition=fake_transition,
        db=SimpleNamespace(
            touch_recording_session_heartbeat=fake_touch_heartbeat,
            merge_recording_session_metadata=fake_merge_metadata,
        ),
    )

    monkeypatch.setenv("ENABLE_AUDIO_RECORDING", "false")
    monkeypatch.setattr(audio_router, "_authenticate_websocket", fake_auth)
    monkeypatch.setattr(audio_router, "state_service", fake_state_service)
    monkeypatch.setattr(audio_router.db, "get_recording_chunk_stats", fake_get_chunk_stats)
    monkeypatch.setattr(
        audio_router.db, "update_recording_session_counters", fake_update_counters
    )
    monkeypatch.setattr(audio_router, "stop_recorder", fake_stop_recorder)
    monkeypatch.setattr(
        audio_router, "get_post_recording_service", lambda: _FakePostService()
    )
    monkeypatch.setattr(audio_router, "AUDIO_CELERY_ENABLED", False)

    ws = FakeWebSocket()
    await audio_router.websocket_streaming_audio(ws, auth_token="test-token")

    message_types = [msg.get("type") for msg in ws.sent]
    assert "connected" in message_types
    assert "pong" in message_types
    assert "stop_ack" in message_types


@pytest.mark.anyio
async def test_websocket_disconnect_honors_persisted_stop_request(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False
            self._messages = [
                {"text": json.dumps({"type": "stop"})},
            ]

        async def accept(self):
            return None

        async def close(self, code=None, reason=None):
            self.closed = True

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive(self):
            if not self._messages:
                raise WebSocketDisconnect()
            return self._messages.pop(0)

    async def fake_auth(_token):
        from app.schemas.user import User

        return User(email="test@appointy.com", name="Test User")

    session_state = {"status": "recording", "stop_requested_at": None}
    finalize_calls = []

    async def fake_ensure_session(**kwargs):
        return None

    async def fake_mark_stop_requested(_session_id):
        session_state["status"] = "stopping_requested"
        session_state["stop_requested_at"] = "2026-03-18T05:37:01Z"
        return True

    async def fake_transition(*args, **kwargs):
        return None

    async def fake_touch_heartbeat(_session_id):
        return None

    async def fake_merge_metadata(*args, **kwargs):
        return None

    async def fake_get_recording_session(_session_id):
        return dict(session_state)

    async def fake_get_chunk_stats(_session_id):
        return {"total": 0, "uploaded": 0}

    async def fake_update_counters(**kwargs):
        return None

    async def fake_stop_recorder(_recorder_key):
        return {}

    async def fake_finalize_session(session_id, flush, process_audio):
        finalize_calls.append(
            {
                "session_id": session_id,
                "flush": flush,
                "process_audio": process_audio,
            }
        )
        audio_router.session_finalized.add(session_id)

    fake_state_service = SimpleNamespace(
        ensure_session=fake_ensure_session,
        mark_stop_requested=fake_mark_stop_requested,
        transition=fake_transition,
        db=SimpleNamespace(
            touch_recording_session_heartbeat=fake_touch_heartbeat,
            merge_recording_session_metadata=fake_merge_metadata,
        ),
    )

    monkeypatch.setenv("ENABLE_AUDIO_RECORDING", "false")
    monkeypatch.setattr(audio_router, "_authenticate_websocket", fake_auth)
    monkeypatch.setattr(audio_router, "state_service", fake_state_service)
    monkeypatch.setattr(audio_router.db, "get_recording_session", fake_get_recording_session)
    monkeypatch.setattr(audio_router.db, "get_recording_chunk_stats", fake_get_chunk_stats)
    monkeypatch.setattr(
        audio_router.db, "update_recording_session_counters", fake_update_counters
    )
    monkeypatch.setattr(audio_router, "stop_recorder", fake_stop_recorder)
    monkeypatch.setattr(audio_router, "_finalize_session", fake_finalize_session)
    monkeypatch.setattr(audio_router, "AUDIO_CELERY_ENABLED", False)

    ws = FakeWebSocket()
    await audio_router.websocket_streaming_audio(ws, auth_token="test-token")

    assert len(finalize_calls) == 1
    assert finalize_calls[0]["flush"] is True
    assert finalize_calls[0]["process_audio"] is True


@pytest.mark.anyio
async def test_get_recording_url_prefers_wav(async_client, monkeypatch):
    async def fake_can(*args, **kwargs):
        return True

    async def fake_exists(path):
        return path.endswith("/recording.wav")

    async def fake_signed_url(path, expiration_seconds=3600, download_filename=None):
        return f"https://example.test/{path}"

    monkeypatch.setattr(audio_router.rbac, "can", fake_can)
    monkeypatch.setattr(audio_router.StorageService, "check_file_exists", fake_exists)
    monkeypatch.setattr(audio_router.StorageService, "generate_signed_url", fake_signed_url)

    meeting_id = "00000000-0000-0000-0000-000000000999"
    response = await async_client.get(f"/meetings/{meeting_id}/recording-url")

    assert response.status_code == 200
    payload = response.json()
    assert payload["url"].endswith(f"{meeting_id}/recording.wav")


@pytest.mark.anyio
async def test_get_recording_url_returns_processing_when_finalize_pending(async_client, monkeypatch):
    async def fake_can(*args, **kwargs):
        return True

    async def fake_exists(_path):
        return False

    async def fake_latest_session(_meeting_id):
        return {
            "session_id": "00000000-0000-0000-0000-000000000888",
            "meeting_id": _meeting_id,
            "status": "finalizing",
        }

    monkeypatch.setattr(audio_router.rbac, "can", fake_can)
    monkeypatch.setattr(audio_router.StorageService, "check_file_exists", fake_exists)
    monkeypatch.setattr(audio_router.db, "get_latest_recording_session_for_meeting", fake_latest_session)

    meeting_id = "00000000-0000-0000-0000-000000000777"
    response = await async_client.get(f"/meetings/{meeting_id}/recording-url")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["session_status"] == "finalizing"


@pytest.mark.anyio
async def test_get_recording_integrity_status_returns_metadata(async_client, monkeypatch):
    async def fake_session(_session_id):
        return {
            "session_id": _session_id,
            "meeting_id": "meeting-123",
            "user_email": "gagan@appointy.com",
            "status": "completed",
            "expected_chunk_count": 12,
            "finalized_chunk_count": 12,
            "dropped_chunk_count": 1,
            "metadata": {
                "recording_integrity": {
                    "finalize_status": "completed",
                    "artifact_path": "meeting-123/recording.wav",
                    "health": {
                        "expected_duration_seconds": 120.0,
                        "actual_duration_seconds": 119.0,
                        "duration_ratio": 0.99,
                        "suspiciously_short": False,
                    },
                }
            },
            "updated_at": None,
        }

    async def fake_chunk_stats(_session_id):
        return {"total": 12, "uploaded": 12, "failed": 0, "pending": 0}

    monkeypatch.setattr(audio_router.db, "get_recording_session", fake_session)
    monkeypatch.setattr(audio_router.db, "get_recording_chunk_stats", fake_chunk_stats)

    response = await async_client.get(
        "/sessions/00000000-0000-0000-0000-000000000555/recording-integrity"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["integrity"]["finalize_status"] == "completed"
    assert payload["chunk_stats"]["uploaded"] == 12


@pytest.mark.anyio
async def test_integrity_report_filters_issue_sessions(async_client, monkeypatch):
    async def fake_sessions(*args, **kwargs):
        return [
            {
                "session_id": "s1",
                "meeting_id": "m1",
                "user_email": "gagan@appointy.com",
                "status": "completed",
                "started_at": datetime.utcnow(),
                "finalized_at": datetime.utcnow(),
                "expected_chunk_count": 12,
                "finalized_chunk_count": 12,
                "dropped_chunk_count": 0,
                "metadata": {
                    "recording_integrity": {
                        "finalize_status": "completed",
                        "health": {"verified": True, "suspiciously_short": False},
                    }
                },
            },
            {
                "session_id": "s2",
                "meeting_id": "m2",
                "user_email": "gagan@appointy.com",
                "status": "completed",
                "started_at": datetime.utcnow(),
                "finalized_at": datetime.utcnow(),
                "expected_chunk_count": 8,
                "finalized_chunk_count": 8,
                "dropped_chunk_count": 2,
                "metadata": {
                    "repair_attempted": True,
                    "recording_integrity": {
                        "finalize_status": "completed",
                        "health": {"verified": True, "suspiciously_short": True},
                    }
                },
            },
        ]

    monkeypatch.setattr(audio_router.db, "list_recording_sessions_since", fake_sessions)

    response = await async_client.get("/recordings/integrity-report?only_issues=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["sessions_returned"] == 1
    assert payload["sessions"][0]["session_id"] == "s2"
    assert payload["sessions"][0]["issue_flags"]["suspiciously_short"] is True


@pytest.mark.anyio
async def test_retry_pipeline_finalize_enqueues_task(async_client, monkeypatch):
    async def fake_session(_session_id):
        return {
            "session_id": _session_id,
            "meeting_id": "meeting-123",
            "user_email": "gagan@appointy.com",
            "status": "failed",
        }

    async def fake_transition(*args, **kwargs):
        return True

    async def fake_merge_metadata(*args, **kwargs):
        return None

    monkeypatch.setattr(audio_router.db, "get_recording_session", fake_session)
    monkeypatch.setattr(audio_router, "AUDIO_CELERY_ENABLED", True)
    monkeypatch.setattr(
        audio_router,
        "state_service",
        SimpleNamespace(
            transition=fake_transition,
            db=SimpleNamespace(merge_recording_session_metadata=fake_merge_metadata),
        ),
    )
    monkeypatch.setattr(
        audio_pipeline_tasks,
        "enqueue_finalize_session_task",
        lambda _sid: "task-123",
    )

    response = await async_client.post(
        "/sessions/00000000-0000-0000-0000-000000000222/retry-finalize"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task-123"


@pytest.mark.anyio
async def test_generate_notes_kicks_off_background_task(async_client, monkeypatch):
    async def fake_can(*args, **kwargs):
        return True

    async def fake_get_meeting(_meeting_id):
        return {"title": "Weekly Sync", "transcripts": [{"text": "hello"}]}

    async def fake_generate_notes(*args, **kwargs):
        return None

    monkeypatch.setattr(transcripts_router.rbac, "can", fake_can)
    monkeypatch.setattr(transcripts_router.db, "get_meeting", fake_get_meeting)
    monkeypatch.setattr(
        transcripts_router,
        "generate_notes_with_gemini_background",
        fake_generate_notes,
    )

    meeting_id = "00000000-0000-0000-0000-000000000123"
    payload = {
        "meeting_id": meeting_id,
        "template_id": "standard_meeting",
        "transcript": "Decision made to ship by Friday.",
        "use_audio_context": False,
    }
    response = await async_client.post(
        f"/meetings/{meeting_id}/generate-notes", json=payload
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "processing"
    assert result["meeting_id"] == meeting_id


def test_notes_prompt_requires_decisions_and_preserves_audio_priority():
    prompt = transcripts_router.get_template_prompt("standard_meeting")

    assert "Every explicit decision or final agreement made during the meeting must be captured in KeyItemsDecisions." in prompt
    assert "If the meeting reaches a decision, KeyItemsDecisions must not be empty." in prompt
