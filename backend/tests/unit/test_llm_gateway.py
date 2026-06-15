"""
Unit tests for the centralized LLM gateway (app/services/llm_gateway.py).

These prove the exact incidents that motivated the gateway are now handled:
  * Gemini model renamed (404 / "model not found")  -> falls back to OpenAI
  * Gemini API key expired (401 / invalid key)        -> falls back to OpenAI
  * Both providers down                               -> clean AllProvidersFailed
  * Circuit breaker opens a repeatedly-failing provider

The provider SDK calls are mocked at the gateway's adapter boundary so no
network or real keys are needed.
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock

import pytest

from app.services import llm_gateway as gw
from app.services.llm_gateway import (
    AllProvidersFailed,
    LLMGateway,
    classify_error,
    resolve_chain,
    should_fallthrough,
)


class _FakeModelNotFound(Exception):
    """Mimics google-genai's not-found error shape."""


class _FakeAuthError(Exception):
    pass


@pytest.fixture
def db_with_keys():
    """A db stub that returns a key for any provider so key-resolution passes."""
    db = AsyncMock()
    db.get_api_key = AsyncMock(return_value="test-key")
    return db


@pytest.fixture(autouse=True)
def clean_env_and_breaker(monkeypatch):
    # Force the canonical default chain regardless of the developer's local env.
    for var in ("LLM_PROVIDER_CHAIN", "LLM_CHAIN_NOTES", "LLM_CHAIN_CHAT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    yield


# ── error classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc,expected",
    [
        (Exception("404 model gemini-x is not found for API version v1beta"), "model_not_found"),
        (Exception("NotFoundError: model does not exist"), "model_not_found"),
        (Exception("401 API key not valid. Please pass a valid API key."), "auth"),
        (Exception("invalid_api_key"), "auth"),
        (Exception("429 RESOURCE_EXHAUSTED quota exceeded"), "rate_limit"),
        (asyncio.TimeoutError(), "timeout"),
        (Exception("503 Service Unavailable: model overloaded"), "server"),
        (Exception("Connection reset by peer"), "network"),
        (Exception("400 invalid request: bad prompt"), "other"),
    ],
)
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


def test_should_fallthrough():
    assert should_fallthrough("model_not_found")
    assert should_fallthrough("auth")
    assert should_fallthrough("rate_limit")
    assert not should_fallthrough("other")  # bad prompt should NOT fall through


def test_resolve_chain_defaults(monkeypatch):
    assert resolve_chain("notes") == ["gemini", "openai"]
    # classify historically preferred OpenAI primary
    assert resolve_chain("classify") == ["openai", "gemini"]


def test_resolve_chain_env_override(monkeypatch):
    monkeypatch.setenv("LLM_CHAIN_NOTES", "openai, gemini")
    assert resolve_chain("notes") == ["openai", "gemini"]
    monkeypatch.setenv("LLM_PROVIDER_CHAIN", "claude,openai")
    monkeypatch.delenv("LLM_CHAIN_CHAT", raising=False)
    assert resolve_chain("chat") == ["claude", "openai"]


# ── the two incidents: model rename + key expiry ─────────────────────────────


@pytest.mark.asyncio
async def test_model_renamed_falls_back_to_openai(db_with_keys, monkeypatch):
    """Gemini raises 'model not found' -> OpenAI answers."""
    gateway = LLMGateway(db_with_keys)

    async def fake_call(*, provider, **kwargs):
        if provider == "gemini":
            raise _FakeModelNotFound("404 gemini-renamed is not found for API version v1")
        if provider == "openai":
            return "answer from openai"
        raise AssertionError(f"unexpected provider {provider}")

    monkeypatch.setattr(gateway, "_call_provider", fake_call)

    out = await gateway.generate(task="notes", prompt="summarize this")
    assert out == "answer from openai"


@pytest.mark.asyncio
async def test_key_expired_falls_back_to_openai(db_with_keys, monkeypatch):
    """Gemini raises 401 invalid/expired key -> OpenAI answers."""
    gateway = LLMGateway(db_with_keys)

    async def fake_call(*, provider, **kwargs):
        if provider == "gemini":
            raise _FakeAuthError("401 API key not valid. Please pass a valid API key.")
        return "openai saved the day"

    monkeypatch.setattr(gateway, "_call_provider", fake_call)
    out = await gateway.generate(task="chat", prompt="hi")
    assert out == "openai saved the day"


@pytest.mark.asyncio
async def test_both_providers_down_raises_all_providers_failed(db_with_keys, monkeypatch):
    gateway = LLMGateway(db_with_keys)

    async def fake_call(*, provider, **kwargs):
        raise Exception("503 service unavailable")

    monkeypatch.setattr(gateway, "_call_provider", fake_call)

    with pytest.raises(AllProvidersFailed) as ei:
        await gateway.generate(task="notes", prompt="x")
    # error map should mention both providers tried
    assert "gemini" in ei.value.errors and "openai" in ei.value.errors


@pytest.mark.asyncio
async def test_non_fallthrough_error_does_not_waste_second_provider(db_with_keys, monkeypatch):
    """A bad-prompt 400 must re-raise immediately, NOT try the next provider."""
    gateway = LLMGateway(db_with_keys)
    calls: List[str] = []

    async def fake_call(*, provider, **kwargs):
        calls.append(provider)
        raise Exception("400 invalid request: prompt too long")

    monkeypatch.setattr(gateway, "_call_provider", fake_call)

    with pytest.raises(Exception):
        await gateway.generate(task="notes", prompt="x")
    assert calls == ["gemini"]  # did not fall through


@pytest.mark.asyncio
async def test_primary_success_never_calls_fallback(db_with_keys, monkeypatch):
    gateway = LLMGateway(db_with_keys)
    calls: List[str] = []

    async def fake_call(*, provider, **kwargs):
        calls.append(provider)
        return "gemini answer"

    monkeypatch.setattr(gateway, "_call_provider", fake_call)
    out = await gateway.generate(task="notes", prompt="x")
    assert out == "gemini answer"
    assert calls == ["gemini"]


# ── circuit breaker ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold(db_with_keys, monkeypatch):
    """After N consecutive gemini failures the breaker skips gemini entirely."""
    monkeypatch.setenv("LLM_CIRCUIT_THRESHOLD", "2")
    monkeypatch.setenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "60")
    gateway = LLMGateway(db_with_keys)

    attempted = []

    async def fake_call(*, provider, **kwargs):
        attempted.append(provider)
        if provider == "gemini":
            raise Exception("503 unavailable")
        return "openai"

    monkeypatch.setattr(gateway, "_call_provider", fake_call)

    # 2 calls trip the gemini breaker (each call: gemini fails -> openai succeeds)
    await gateway.generate(task="notes", prompt="1")
    await gateway.generate(task="notes", prompt="2")
    attempted.clear()
    # 3rd call: gemini circuit is open -> gemini is skipped, only openai attempted
    await gateway.generate(task="notes", prompt="3")
    assert "gemini" not in attempted
    assert attempted == ["openai"]


# ── streaming fallback ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_falls_back_before_first_token(db_with_keys, monkeypatch):
    gateway = LLMGateway(db_with_keys)

    async def fake_stream(*, provider, **kwargs):
        if provider == "gemini":
            raise Exception("401 invalid api key")
            yield  # make it a generator
        for tok in ["hel", "lo"]:
            yield tok

    monkeypatch.setattr(gateway, "_stream_provider", fake_stream)

    out = "".join([c async for c in gateway.stream(task="chat", prompt="hi")])
    assert out == "hello"


@pytest.mark.asyncio
async def test_stream_midstream_failure_does_not_silently_swap(db_with_keys, monkeypatch):
    """Once tokens are emitted, a failure must surface (no silent provider swap)."""
    gateway = LLMGateway(db_with_keys)

    async def fake_stream(*, provider, **kwargs):
        if provider == "gemini":
            yield "partial "
            raise Exception("503 unavailable mid stream")
        yield "should-not-be-used"

    monkeypatch.setattr(gateway, "_stream_provider", fake_stream)

    collected = []
    with pytest.raises(Exception):
        async for c in gateway.stream(task="chat", prompt="hi"):
            collected.append(c)
    assert collected == ["partial "]
