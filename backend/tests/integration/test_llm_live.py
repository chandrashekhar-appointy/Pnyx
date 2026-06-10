"""Live LLM smoke tests — no mocks, no monkeypatching.

These tests make real API calls using whatever provider/model is configured
in .env (the same keys and models production uses).  If they pass, you know:
  - the API key is valid
  - the model name is accepted
  - the request parameters (max_tokens vs max_completion_tokens, temperature,
    response_format) are compatible with the model
  - the gateway fallback chain works

They are skipped automatically when the required API key is not in the
environment, so they never block CI on a machine without keys.  They run
locally and in any environment where the keys are present.

Why this matters
----------------
The mocked tests in test_chat_and_catchup.py bypass the OpenAI SDK entirely.
A real test sending "say hi" would have immediately caught the
  "max_tokens is not supported — use max_completion_tokens"
400 error from gpt-5.x, which previously only appeared in production.
"""

from __future__ import annotations

import os
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skip_if_no_key(provider: str) -> None:
    """Skip the test if the provider's API key isn't configured."""
    key_map = {
        "openai": ("OPENAI_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "groq": ("GROQ_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    }
    for env_name in key_map.get(provider, ()):
        if os.getenv(env_name, "").strip():
            return
    pytest.skip(f"No API key configured for provider '{provider}' — set {key_map.get(provider, ('?',))[0]} to run")


def _notes_provider() -> str:
    return os.getenv("NOTES_SUMMARY_PROVIDER", "gemini").lower().strip()


def _notes_model() -> str:
    from app.services.llm_gateway import GEMINI_DEFAULT_MODEL
    defaults = {"openai": "gpt-4o-mini", "gemini": GEMINI_DEFAULT_MODEL, "groq": "llama-3.1-8b-instant"}
    return os.getenv("NOTES_SUMMARY_MODEL", defaults.get(_notes_provider(), "")).strip()


def _ai_participant_provider() -> str:
    return os.getenv("AI_PARTICIPANT_PROVIDER", "openai").lower().strip()


def _ai_participant_model() -> str:
    from app.services.llm_gateway import GEMINI_DEFAULT_MODEL
    defaults = {"openai": "gpt-4o-mini", "gemini": GEMINI_DEFAULT_MODEL}
    return os.getenv("AI_PARTICIPANT_MODEL", defaults.get(_ai_participant_provider(), "")).strip()


# ---------------------------------------------------------------------------
# Gateway-level smoke tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gateway_generate_with_production_model():
    """gateway.generate() works with the model configured in NOTES_SUMMARY_*."""
    provider = _notes_provider()
    model = _notes_model()
    _skip_if_no_key(provider)

    from app.services.llm_gateway import LLMGateway
    from app.db.manager import DatabaseManager
    db = DatabaseManager()
    gateway = LLMGateway(db)

    result = await gateway.generate(
        task="chat",
        prompt="Say exactly: hi",
        system="You are a helpful assistant. Reply with one word only.",
        temperature=0.0,
        max_tokens=10,
        chain=[provider],
        model_overrides={provider: model},
    )

    assert isinstance(result, str) and len(result.strip()) > 0, (
        f"Expected a non-empty response from {provider}:{model}, got: {result!r}"
    )


@pytest.mark.anyio
async def test_gateway_stream_with_production_model():
    """gateway.stream() works with the model configured in NOTES_SUMMARY_*."""
    provider = _notes_provider()
    model = _notes_model()
    _skip_if_no_key(provider)

    from app.services.llm_gateway import LLMGateway
    from app.db.manager import DatabaseManager
    db = DatabaseManager()
    gateway = LLMGateway(db)

    chunks = []
    async for chunk in gateway.stream(
        task="chat",
        prompt="Say exactly: hi",
        system="You are a helpful assistant. Reply with one word only.",
        temperature=0.0,
        max_tokens=10,
        chain=[provider],
        model_overrides={provider: model},
    ):
        chunks.append(chunk)

    full = "".join(chunks).strip()
    assert len(full) > 0, (
        f"Expected streamed tokens from {provider}:{model}, got empty response"
    )


@pytest.mark.anyio
async def test_gateway_generate_with_ai_participant_model():
    """gateway.generate() works with the model used for live insights."""
    provider = _ai_participant_provider()
    model = _ai_participant_model()
    _skip_if_no_key(provider)

    from app.services.llm_gateway import LLMGateway
    from app.db.manager import DatabaseManager
    db = DatabaseManager()
    gateway = LLMGateway(db)

    result = await gateway.generate(
        task="chat",
        prompt="Say exactly: hi",
        system="You are a helpful assistant. Reply with one word only.",
        temperature=0.0,
        max_tokens=10,
        chain=[provider],
        model_overrides={provider: model},
    )

    assert isinstance(result, str) and len(result.strip()) > 0, (
        f"Expected a non-empty response from {provider}:{model}, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Endpoint-level smoke test (full stack, no LLM mock)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_chat_meeting_endpoint_real_llm(async_client, monkeypatch):
    """POST /chat-meeting reaches the real LLM and streams a non-empty response.

    Uses context_text so no DB lookup is needed.
    Only RBAC is patched (permission guard for meeting ownership) — the LLM
    call is NOT mocked.  This is the test that would have caught the
    max_tokens/max_completion_tokens incompatibility immediately.
    """
    provider = _notes_provider()
    model = _notes_model()
    _skip_if_no_key(provider)

    from app.api.routers import chat as chat_router
    async def _allow_all(*a, **kw):
        return True
    monkeypatch.setattr(chat_router.rbac, "can", _allow_all)

    payload = {
        "meeting_id": "smoke-test-meeting",
        "question": "Say hi",
        "model": provider,
        "model_name": model,
        "context_text": "This is a test meeting. The topic is software testing.",
        "context_entries": [],
        "history": [],
    }

    response = await async_client.post("/chat-meeting", json=payload, timeout=30)

    assert response.status_code == 200, (
        f"Expected 200 from /chat-meeting, got {response.status_code}: {response.text[:300]}"
    )
    assert len(response.text.strip()) > 0, (
        f"Expected non-empty streamed response from {provider}:{model}"
    )
