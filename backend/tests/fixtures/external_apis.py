"""Centralized respx-based mocks for every external API the backend touches.

External services: Groq, ElevenLabs, OpenAI, Gemini, Anthropic, Tavily, Razorpay,
and Google JWKS.  All clients use httpx under the hood (Groq/OpenAI SDKs build
on it), so respx routes intercept everything regardless of the SDK wrapper.

A single `external_apis_mock` fixture installs the default happy-path routes;
individual tests override specific routes for failure-mode coverage.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

import httpx
import respx

from tests.fixtures.jwt_helpers import encoded_jwks_json

# ----------------------------- Default response payloads --------------------

DEFAULT_TRANSCRIPT_TEXT = (
    "Hello team, this is a synthetic transcript used by the test suite."
)


def _groq_transcription_payload(text: str = DEFAULT_TRANSCRIPT_TEXT) -> dict[str, Any]:
    return {
        "text": text,
        "language": "en",
        "duration": 5.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 5.0,
                "text": text,
                "avg_logprob": -0.2,
                "no_speech_prob": 0.01,
            }
        ],
    }


def _elevenlabs_transcription_payload(
    text: str = DEFAULT_TRANSCRIPT_TEXT,
) -> dict[str, Any]:
    return {
        "text": text,
        "language_code": "eng",
        "language_probability": 0.99,
        "words": [
            {"text": w, "start": i * 0.5, "end": (i + 1) * 0.5, "type": "word"}
            for i, w in enumerate(text.split())
        ],
    }


def _openai_chat_payload(content: str = "Test response.") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _gemini_generate_payload(text: str = "Generated summary.") -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    }


def _anthropic_message_payload(text: str = "Test response.") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-test",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _tavily_search_payload() -> dict[str, Any]:
    return {
        "query": "test",
        "answer": "Synthetic search answer.",
        "results": [
            {
                "title": "Test Source",
                "url": "https://example.test/result",
                "content": "Mocked search result snippet.",
                "score": 0.9,
            }
        ],
    }


def _razorpay_order_payload() -> dict[str, Any]:
    return {
        "id": "order_test",
        "entity": "order",
        "amount": 1000,
        "currency": "INR",
        "status": "created",
    }


# ----------------------------- Route registration --------------------------


class ExternalApiMocks:
    """Wraps respx.MockRouter and exposes an install/reset API."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self._installed: list[respx.Route] = []

    def install_defaults(self) -> "ExternalApiMocks":
        # Google JWKS — used by app.core.security.verify_google_token
        self.router.get("https://www.googleapis.com/oauth2/v3/certs").mock(
            return_value=httpx.Response(
                200,
                content=encoded_jwks_json(),
                headers={"content-type": "application/json"},
            )
        )

        # Groq — transcription + chat endpoints
        self.router.post("https://api.groq.com/openai/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json=_groq_transcription_payload())
        )
        self.router.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_payload())
        )

        # ElevenLabs — Scribe v2 (batch transcription)
        self.router.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(200, json=_elevenlabs_transcription_payload())
        )

        # OpenAI — chat completions
        self.router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_payload())
        )

        # Gemini — generative content
        self.router.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        ).mock(return_value=httpx.Response(200, json=_gemini_generate_payload()))
        # Catch-all for any Gemini model
        self.router.post(
            url__regex=r"^https://generativelanguage\.googleapis\.com/.*generateContent.*$"
        ).mock(return_value=httpx.Response(200, json=_gemini_generate_payload()))

        # Anthropic — messages
        self.router.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_message_payload())
        )

        # Tavily — search
        self.router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(200, json=_tavily_search_payload())
        )

        # Razorpay — orders + verification
        self.router.post("https://api.razorpay.com/v1/orders").mock(
            return_value=httpx.Response(200, json=_razorpay_order_payload())
        )

        return self

    # ----- Failure-mode helpers -------------------------------------------

    def fail_groq(self, status: int = 500, message: str = "internal_error") -> None:
        self.router.post("https://api.groq.com/openai/v1/audio/transcriptions").mock(
            return_value=httpx.Response(
                status, json={"error": {"message": message, "type": "server_error"}}
            )
        )

    def fail_elevenlabs(self, status: int = 401) -> None:
        self.router.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(status, json={"detail": {"message": "auth"}})
        )

    def slow_groq(self, delay_seconds: float = 5.0) -> None:
        async def _slow(_request: httpx.Request) -> httpx.Response:
            import asyncio

            await asyncio.sleep(delay_seconds)
            return httpx.Response(200, json=_groq_transcription_payload())

        self.router.post(
            "https://api.groq.com/openai/v1/audio/transcriptions"
        ).mock(side_effect=_slow)

    def custom_transcript(self, text: str) -> None:
        self.router.post("https://api.groq.com/openai/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json=_groq_transcription_payload(text))
        )
        self.router.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(
                200, json=_elevenlabs_transcription_payload(text)
            )
        )

    def custom_llm_response(self, content: str) -> None:
        self.router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_payload(content))
        )
        self.router.post(
            url__regex=r"^https://generativelanguage\.googleapis\.com/.*generateContent.*$"
        ).mock(return_value=httpx.Response(200, json=_gemini_generate_payload(content)))
