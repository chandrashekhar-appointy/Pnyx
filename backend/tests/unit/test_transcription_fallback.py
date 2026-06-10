"""
Unit tests for TranscriptionFallbackClient (app/services/audio/transcription_fallback.py).

Proves the ElevenLabs -> Groq failover behavior:
  * EL 401 (exhausted key)  -> Groq transcribes the chunk, on_fallback fires once
  * EL rate_limit_exceeded  -> Groq takes over
  * sticks on Groq for subsequent chunks (no re-probe within the window)
  * re-probes EL after the window and switches back when EL is healthy
  * both providers failing surfaces a single combined error result
"""

import pytest

from app.services.audio.transcription_fallback import (
    TranscriptionFallbackClient,
    _is_provider_failure,
)


class FakeClient:
    """Minimal stand-in with the transcription interface the wrapper calls."""

    def __init__(self, provider: str, results):
        self.provider = provider
        # results: list of dicts/exceptions to return in sequence; last repeats.
        self._results = list(results)
        self.calls = 0

    def _next(self):
        if self.calls < len(self._results):
            r = self._results[self.calls]
        else:
            r = self._results[-1]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r

    async def transcribe_audio_async(self, *args, **kwargs):
        return self._next()

    async def transcribe_full_audio(self, *args, **kwargs):
        return self._next()


OK = {"text": "hello", "confidence": 0.9}
SILENCE = {"text": "", "confidence": 0.0}  # not an error — just silence
EL_401 = {"text": "", "confidence": 0.0, "error": "ElevenLabs 401: invalid_api_key"}
EL_429 = {"text": "", "confidence": 0.0, "error": "rate_limit_exceeded"}
GROQ_OK = {"text": "groq text", "confidence": 0.8}


# ── failure detection ─────────────────────────────────────────────────────────


def test_is_provider_failure():
    assert _is_provider_failure(EL_401)
    assert _is_provider_failure(EL_429)
    assert _is_provider_failure(None)
    assert not _is_provider_failure(OK)
    assert not _is_provider_failure(SILENCE)  # empty != failure


# ── core failover ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_401_falls_back_to_groq_and_fires_callback():
    primary = FakeClient("elevenlabs", [EL_401])
    fallback = FakeClient("groq", [GROQ_OK])
    events = []

    wrapper = TranscriptionFallbackClient(
        primary, fallback, on_fallback=lambda p, u, r: events.append((p, u, r))
    )

    result = await wrapper.transcribe_audio_async(b"audio")
    assert result == GROQ_OK
    assert wrapper.fell_back is True
    assert wrapper.provider == "groq"
    assert events == [("elevenlabs", "groq", "primary_failed")]


@pytest.mark.asyncio
async def test_el_rate_limit_falls_back():
    primary = FakeClient("elevenlabs", [EL_429])
    fallback = FakeClient("groq", [GROQ_OK])
    wrapper = TranscriptionFallbackClient(primary, fallback)
    result = await wrapper.transcribe_audio_async(b"a")
    assert result == GROQ_OK
    assert wrapper.fell_back


@pytest.mark.asyncio
async def test_sticks_on_groq_without_reprobe():
    # EL would succeed if retried, but within the re-probe window we must NOT
    # call it again — stay on Groq.
    primary = FakeClient("elevenlabs", [EL_401, OK])
    fallback = FakeClient("groq", [GROQ_OK, GROQ_OK, GROQ_OK])
    # Large window so no re-probe happens.
    wrapper = TranscriptionFallbackClient(primary, fallback, reprobe_seconds=10_000)

    await wrapper.transcribe_audio_async(b"1")  # EL fails -> Groq
    el_calls_after_first = primary.calls
    await wrapper.transcribe_audio_async(b"2")  # stays on Groq
    await wrapper.transcribe_audio_async(b"3")  # stays on Groq

    assert wrapper.fell_back
    # EL was not called again after the initial failure.
    assert primary.calls == el_calls_after_first
    assert fallback.calls == 3


@pytest.mark.asyncio
async def test_reprobe_recovers_to_primary():
    # First call: EL fails -> Groq. With reprobe_seconds=0 the next call
    # re-probes EL, which is now healthy -> switch back to EL.
    primary = FakeClient("elevenlabs", [EL_401, OK, OK])
    fallback = FakeClient("groq", [GROQ_OK])
    recovered = []
    wrapper = TranscriptionFallbackClient(
        primary, fallback, reprobe_seconds=0, on_recover=lambda p: recovered.append(p)
    )

    r1 = await wrapper.transcribe_audio_async(b"1")
    assert r1 == GROQ_OK and wrapper.fell_back

    r2 = await wrapper.transcribe_audio_async(b"2")  # re-probe EL -> healthy
    assert r2 == OK
    assert wrapper.fell_back is False
    assert wrapper.provider == "elevenlabs"
    assert recovered == ["elevenlabs"]


@pytest.mark.asyncio
async def test_both_fail_surfaces_combined_error():
    primary = FakeClient("elevenlabs", [EL_401])
    fallback = FakeClient("groq", [{"text": "", "error": "503 unavailable"}])
    wrapper = TranscriptionFallbackClient(primary, fallback)

    result = await wrapper.transcribe_audio_async(b"a")
    assert result.get("error")  # an error IS surfaced when both fail
    # the surfaced result is the last provider's failure dict
    assert "503" in str(result["error"]) or "unavailable" in str(result["error"])


@pytest.mark.asyncio
async def test_primary_success_never_calls_fallback():
    primary = FakeClient("elevenlabs", [OK])
    fallback = FakeClient("groq", [GROQ_OK])
    wrapper = TranscriptionFallbackClient(primary, fallback)
    result = await wrapper.transcribe_audio_async(b"a")
    assert result == OK
    assert not wrapper.fell_back
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_primary_exception_also_falls_back():
    primary = FakeClient("elevenlabs", [RuntimeError("connection reset")])
    fallback = FakeClient("groq", [GROQ_OK])
    wrapper = TranscriptionFallbackClient(primary, fallback)
    result = await wrapper.transcribe_audio_async(b"a")
    assert result == GROQ_OK
    assert wrapper.fell_back
