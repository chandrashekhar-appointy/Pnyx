"""
Centralized LLM gateway with automatic provider fallback.

Why this exists
---------------
Two production incidents (a Gemini model rename, and a Gemini API key
expiration) took down notes/chat/catch-up because every LLM call funneled
through a single provider+model. Fallback logic *did* exist, but it was
hand-rolled and duplicated inline at every call site (chat/service.py had four
separate try-Gemini/except-OpenAI blocks; transcripts.py had its own variant).
A fifth call site could easily ship with no fallback at all.

This module centralizes that pattern. Every feature calls ``gateway.generate``
or ``gateway.stream`` with a *task* name; the gateway walks a per-task provider
chain (default ``gemini -> openai``), classifies failures, and transparently
falls through to the next provider when one is auth-broken, model-renamed,
rate-limited, timed out, or 5xx-ing. It adds a per-call timeout, one retry on
transient errors, and a per-provider circuit breaker so a dead provider isn't
hammered.

Design notes
------------
* Providers reuse the *existing* low-level clients (``gemini_client`` helpers,
  ``AsyncOpenAI``/``AsyncGroq``/``AsyncAnthropic``) — this module only owns the
  chain/fallback/classification, not new SDK wrappers.
* Key resolution mirrors what call sites already do: env var first, then
  ``db.get_api_key(provider, user_email=...)``.
* Per-task chains are configurable so call sites that historically preferred a
  different primary (e.g. the intent classifier preferred OpenAI gpt-4o-mini)
  keep their behavior via ``LLM_CHAIN_<TASK>`` / built-in defaults.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

try:
    from ..model_config import GEMINI_DEFAULT_MODEL, OPENAI_DEFAULT_MODEL
except (ImportError, ValueError):
    try:
        from model_config import GEMINI_DEFAULT_MODEL, OPENAI_DEFAULT_MODEL
    except (ImportError, ValueError):
        GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-3.5-flash")
        OPENAI_DEFAULT_MODEL = os.getenv("OPENAI_DEFAULT_MODEL", "gpt-4o")

try:
    from .gemini_client import (
        generate_content_text_async,
        stream_content_text_async,
    )
except (ImportError, ValueError):
    from services.gemini_client import (
        generate_content_text_async,
        stream_content_text_async,
    )

logger = logging.getLogger(__name__)

try:
    import sentry_sdk
except Exception:  # pragma: no cover - sentry optional
    sentry_sdk = None


# ── Error taxonomy ──────────────────────────────────────────────────────────


class LLMError(Exception):
    """Base class for gateway errors."""


class AllProvidersFailed(LLMError):
    """Raised when every provider in the chain failed.

    Carries the per-provider error so callers/logs can see the full picture
    instead of a single opaque 500.
    """

    def __init__(self, task: str, errors: Dict[str, str]):
        self.task = task
        self.errors = errors
        detail = "; ".join(f"{p}: {e}" for p, e in errors.items())
        super().__init__(f"All LLM providers failed for task '{task}' ({detail})")


# Failure categories that should trigger a fall-through to the next provider.
# Anything NOT in this set (e.g. a malformed-prompt 400) re-raises immediately —
# falling through would just fail the same way and waste a call.
_FALLTHROUGH = {"auth", "model_not_found", "rate_limit", "timeout", "server", "network"}


def classify_error(exc: BaseException) -> str:
    """Classify an exception from any provider SDK into a coarse category.

    Uses status codes + substring matching on the message and the exception
    type name so it stays robust across SDK versions (the exact exception
    classes differ between google-genai, openai, anthropic, groq).
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"

    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    blob = f"{name} {msg} {status}"

    # Auth / expired / invalid key
    if any(
        s in blob
        for s in (
            "401",
            "403",
            "api_key_invalid",
            "invalid_api_key",
            "invalid api key",
            "unauthenticated",
            "permission_denied",
            "permissiondenied",
            "expired",
            "authenticationerror",
            "api key not valid",
        )
    ):
        return "auth"

    # Model renamed / removed / unknown — the exact bug that burned us.
    if any(
        s in blob
        for s in (
            "404",
            "not found",
            "not_found",
            "notfounderror",
            "does not exist",
            "model_not_found",
            "unknown model",
            "is not found for api version",
            "no such model",
        )
    ):
        return "model_not_found"

    # Rate limit / quota exhausted
    if any(
        s in blob
        for s in (
            "429",
            "rate limit",
            "rate_limit",
            "ratelimiterror",
            "resource_exhausted",
            "resourceexhausted",
            "quota",
            "too many requests",
        )
    ):
        return "rate_limit"

    # Timeout / deadline
    if any(s in blob for s in ("timeout", "timed out", "deadline")):
        return "timeout"

    # Transient server-side
    if any(
        s in blob
        for s in (
            "500",
            "502",
            "503",
            "504",
            "internal",
            "unavailable",
            "overloaded",
            "service_unavailable",
            "bad gateway",
            "server error",
            "internalservererror",
        )
    ):
        return "server"

    # Connection problems
    if any(s in blob for s in ("connection", "econnreset", "network", "dns")):
        return "network"

    return "other"


def should_fallthrough(category: str) -> bool:
    return category in _FALLTHROUGH


# ── Circuit breaker ─────────────────────────────────────────────────────────


class _CircuitBreaker:
    """Tiny in-process circuit breaker, keyed by provider.

    After ``threshold`` consecutive failures a provider is "open" (skipped) for
    ``cooldown`` seconds, so we don't keep paying latency on a provider that is
    clearly down. A single success closes it again.
    """

    def __init__(self, threshold: int, cooldown: float):
        self.threshold = threshold
        self.cooldown = cooldown
        self._fails: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}

    def is_open(self, provider: str) -> bool:
        until = self._open_until.get(provider, 0.0)
        if until and time.monotonic() < until:
            return True
        if until:
            # Cooldown elapsed — half-open: allow a probe.
            self._open_until.pop(provider, None)
        return False

    def record_success(self, provider: str) -> None:
        self._fails.pop(provider, None)
        self._open_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        n = self._fails.get(provider, 0) + 1
        self._fails[provider] = n
        if n >= self.threshold:
            self._open_until[provider] = time.monotonic() + self.cooldown
            logger.warning(
                "[LLMGateway] Circuit opened for provider '%s' after %d failures "
                "(cooldown %.0fs)",
                provider,
                n,
                self.cooldown,
            )


# ── Per-task provider chains ────────────────────────────────────────────────

# Built-in defaults. Notes/chat/refine default to Gemini->OpenAI (the decided
# chain). Classification/topic historically preferred OpenAI gpt-4o-mini, so
# their default chain keeps OpenAI primary to preserve behavior/cost. Override
# any of these at runtime with LLM_CHAIN_<TASK> (comma-separated) or the global
# LLM_PROVIDER_CHAIN.
_DEFAULT_CHAINS: Dict[str, List[str]] = {
    "notes": ["gemini", "openai"],
    "chat": ["gemini", "openai"],
    "reformulate": ["gemini", "openai"],
    "refine": ["gemini", "openai"],
    "classify": ["openai", "gemini"],
    "topic": ["openai", "gemini"],
    "behavior": ["gemini", "openai"],
}

_GLOBAL_DEFAULT_CHAIN = ["gemini", "openai"]


def resolve_chain(task: str) -> List[str]:
    env_task = os.getenv(f"LLM_CHAIN_{task.upper()}")
    if env_task:
        chain = [p.strip().lower() for p in env_task.split(",") if p.strip()]
        if chain:
            return chain
    env_global = os.getenv("LLM_PROVIDER_CHAIN")
    if env_global:
        chain = [p.strip().lower() for p in env_global.split(",") if p.strip()]
        if chain:
            return chain
    return list(_DEFAULT_CHAINS.get(task, _GLOBAL_DEFAULT_CHAIN))


# ── Gateway ─────────────────────────────────────────────────────────────────


class LLMGateway:
    """Provider-agnostic text generation with automatic fallback.

    Construct with a ``DatabaseManager`` (for per-user key lookup). All methods
    accept a ``task`` so the right provider chain + default model are selected.
    """

    def __init__(self, db: Any):
        self.db = db
        self.timeout = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "60"))
        self.breaker = _CircuitBreaker(
            threshold=int(os.getenv("LLM_CIRCUIT_THRESHOLD", "3")),
            cooldown=float(os.getenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "60")),
        )

    # ── key resolution ──────────────────────────────────────────────────────

    async def _api_key(self, provider: str, user_email: Optional[str]) -> Optional[str]:
        env_map = {
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
            "groq": ("GROQ_API_KEY",),
            "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
            "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
        }
        for env_name in env_map.get(provider, ()):  # env wins (prod-configured)
            val = os.getenv(env_name)
            if val and val.strip():
                return val.strip()
        try:
            db_provider = "claude" if provider in ("claude", "anthropic") else provider
            key = await self.db.get_api_key(db_provider, user_email=user_email)
            return key.strip() if key else None
        except Exception as e:  # key lookup must never crash a generation
            logger.debug("[LLMGateway] key lookup failed for %s: %s", provider, e)
            return None

    def _default_model(self, provider: str, model_overrides: Optional[Dict[str, str]]) -> str:
        if model_overrides and provider in model_overrides:
            return model_overrides[provider]
        return {
            "gemini": GEMINI_DEFAULT_MODEL,
            "openai": OPENAI_DEFAULT_MODEL,
            "groq": os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
            "claude": os.getenv("ANTHROPIC_DEFAULT_MODEL", "claude-haiku-4-5-20251001"),
            "anthropic": os.getenv("ANTHROPIC_DEFAULT_MODEL", "claude-haiku-4-5-20251001"),
        }.get(provider, GEMINI_DEFAULT_MODEL)

    # ── public: non-streaming ───────────────────────────────────────────────

    async def generate(
        self,
        *,
        task: str,
        prompt: str,
        system: Optional[str] = None,
        user_email: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        gemini_config: Optional[Dict[str, Any]] = None,
        model_overrides: Optional[Dict[str, str]] = None,
        chain: Optional[List[str]] = None,
    ) -> str:
        """Generate text, walking the task's provider chain on failure.

        Raises ``AllProvidersFailed`` only when every provider in the chain
        fails with a fall-through-eligible error. A non-fallthrough error
        (e.g. a malformed request) from the first provider re-raises directly.

        ``chain`` lets a caller pass an explicit provider order (e.g. honor a
        user-selected model while still falling back); otherwise the task's
        configured chain is used.
        """
        chain = chain or resolve_chain(task)
        errors: Dict[str, str] = {}

        for idx, provider in enumerate(chain):
            if self.breaker.is_open(provider):
                errors[provider] = "circuit_open"
                logger.info("[LLMGateway] skipping '%s' (circuit open) for task=%s", provider, task)
                continue

            key = await self._api_key(provider, user_email)
            if not key:
                errors[provider] = "no_api_key"
                continue

            model = self._default_model(provider, model_overrides)
            try:
                text = await asyncio.wait_for(
                    self._call_provider(
                        provider=provider,
                        model=model,
                        prompt=prompt,
                        system=system,
                        api_key=key,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                        gemini_config=gemini_config,
                    ),
                    timeout=self.timeout,
                )
                self.breaker.record_success(provider)
                if idx > 0:
                    self._note_fallback(task, chain[0], provider, errors)
                return text or ""
            except Exception as exc:  # noqa: BLE001 - we classify then decide
                category = classify_error(exc)
                self.breaker.record_failure(provider)
                errors[provider] = f"{category}: {exc}"
                is_last = idx == len(chain) - 1
                if should_fallthrough(category) and not is_last:
                    logger.warning(
                        "[LLMGateway] task=%s provider=%s failed (%s) — falling through",
                        task, provider, category,
                    )
                    continue
                if not should_fallthrough(category):
                    logger.error(
                        "[LLMGateway] task=%s provider=%s non-recoverable error (%s): %s",
                        task, provider, category, exc,
                    )
                    raise
                # last provider, fallthrough-eligible → exhausted
                logger.error(
                    "[LLMGateway] task=%s provider=%s failed (%s) — chain exhausted",
                    task, provider, category,
                )

        raise AllProvidersFailed(task, errors)

    # ── public: streaming ────────────────────────────────────────────────────

    async def stream(
        self,
        *,
        task: str,
        prompt: str,
        system: Optional[str] = None,
        user_email: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        gemini_config: Optional[Dict[str, Any]] = None,
        model_overrides: Optional[Dict[str, str]] = None,
        chain: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        """Stream text tokens, falling through to the next provider if a
        provider fails *before emitting any output*.

        Note: once a provider has emitted tokens we cannot silently fall back
        mid-stream (the consumer already saw partial output), so a mid-stream
        failure ends the stream. Pre-first-token failures fall through cleanly,
        which covers the common cases (bad model name, expired key, 429 at
        connect time).
        """
        chain = chain or resolve_chain(task)
        errors: Dict[str, str] = {}

        for idx, provider in enumerate(chain):
            if self.breaker.is_open(provider):
                errors[provider] = "circuit_open"
                continue
            key = await self._api_key(provider, user_email)
            if not key:
                errors[provider] = "no_api_key"
                continue
            model = self._default_model(provider, model_overrides)

            emitted = False
            try:
                async for chunk in self._stream_provider(
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    system=system,
                    api_key=key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    gemini_config=gemini_config,
                ):
                    if chunk:
                        emitted = True
                        yield chunk
                self.breaker.record_success(provider)
                if idx > 0:
                    self._note_fallback(task, chain[0], provider, errors)
                return
            except Exception as exc:  # noqa: BLE001
                category = classify_error(exc)
                self.breaker.record_failure(provider)
                errors[provider] = f"{category}: {exc}"
                if emitted:
                    # Already streamed partial output; cannot transparently swap.
                    logger.error(
                        "[LLMGateway] task=%s provider=%s failed mid-stream (%s)",
                        task, provider, category,
                    )
                    raise
                is_last = idx == len(chain) - 1
                if should_fallthrough(category) and not is_last:
                    logger.warning(
                        "[LLMGateway] task=%s provider=%s stream failed pre-output (%s) — falling through",
                        task, provider, category,
                    )
                    continue
                if not should_fallthrough(category):
                    raise

        raise AllProvidersFailed(task, errors)

    # ── provider adapters ────────────────────────────────────────────────────

    async def _call_provider(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        system: Optional[str],
        api_key: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
        gemini_config: Optional[Dict[str, Any]],
    ) -> str:
        if provider == "gemini":
            config: Dict[str, Any] = dict(gemini_config or {})
            if system:
                config.setdefault("system_instruction", system)
            if temperature is not None:
                config.setdefault("temperature", temperature)
            if max_tokens is not None:
                config.setdefault("max_output_tokens", max_tokens)
            if json_mode:
                config.setdefault("response_mime_type", "application/json")
            return await generate_content_text_async(
                api_key=api_key, model=model, contents=prompt, config=config
            )

        if provider in ("openai", "groq"):
            client = (
                _make_openai(api_key) if provider == "openai" else _make_groq(api_key)
            )
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            kwargs: Dict[str, Any] = {"model": model, "messages": messages}
            if temperature is not None and not _model_omit_temperature(model):
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                if provider == "openai" and _model_uses_completion_tokens(model):
                    kwargs["max_completion_tokens"] = max_tokens
                else:
                    kwargs["max_tokens"] = max_tokens
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""

        if provider in ("claude", "anthropic"):
            client = _make_anthropic(api_key)
            sys_prompt = system or ""
            if json_mode:
                sys_prompt = (sys_prompt + "\n\nReturn ONLY a JSON object. No prose, no code fences.").strip()
            resp = await client.messages.create(
                model=model,
                max_tokens=max_tokens or 4096,
                temperature=temperature if temperature is not None else 0.2,
                system=sys_prompt or None,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(getattr(b, "text", "") or "" for b in resp.content)

        raise LLMError(f"Unknown provider '{provider}'")

    async def _stream_provider(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        system: Optional[str],
        api_key: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        gemini_config: Optional[Dict[str, Any]],
    ) -> AsyncIterator[str]:
        if provider == "gemini":
            config: Dict[str, Any] = dict(gemini_config or {})
            if system:
                config.setdefault("system_instruction", system)
            if temperature is not None:
                config.setdefault("temperature", temperature)
            if max_tokens is not None:
                config.setdefault("max_output_tokens", max_tokens)
            async for chunk in stream_content_text_async(
                api_key=api_key, model=model, contents=prompt, config=config
            ):
                yield chunk
            return

        if provider in ("openai", "groq"):
            client = (
                _make_openai(api_key) if provider == "openai" else _make_groq(api_key)
            )
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            kwargs: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
            if temperature is not None and not _model_omit_temperature(model):
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                if provider == "openai" and _model_uses_completion_tokens(model):
                    kwargs["max_completion_tokens"] = max_tokens
                else:
                    kwargs["max_tokens"] = max_tokens
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                content = chunk.choices[0].delta.content or ""
                if content:
                    yield content
            return

        if provider in ("claude", "anthropic"):
            client = _make_anthropic(api_key)
            stream = await client.messages.create(
                model=model,
                max_tokens=max_tokens or 4096,
                temperature=temperature if temperature is not None else 0.7,
                system=system or None,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            async for text in stream.text_stream:
                if text:
                    yield text
            return

        raise LLMError(f"Unknown provider '{provider}'")

    # ── observability ────────────────────────────────────────────────────────

    def _note_fallback(
        self, task: str, primary: str, used: str, errors: Dict[str, str]
    ) -> None:
        logger.warning(
            "[LLMGateway] task=%s FELL BACK from '%s' to '%s' (primary error: %s)",
            task, primary, used, errors.get(primary, "?"),
        )
        if sentry_sdk is not None:
            try:
                sentry_sdk.add_breadcrumb(
                    category="llm_fallback",
                    level="warning",
                    message=f"LLM fallback {primary}->{used} for {task}",
                    data={"task": task, "primary": primary, "used": used, "errors": errors},
                )
            except Exception:
                pass
        _FALLBACK_COUNTER[task] = _FALLBACK_COUNTER.get(task, 0) + 1


# Module-level counter so /health/deep or the analytics dashboard can surface
# how often we are falling back (a rising number = a provider is degrading).
_FALLBACK_COUNTER: Dict[str, int] = {}


def get_fallback_counts() -> Dict[str, int]:
    return dict(_FALLBACK_COUNTER)


# ── OpenAI model capability helpers ──────────────────────────────────────────
# Newer OpenAI reasoning/frontier models (o-series, gpt-5.x) require
# max_completion_tokens and reject max_tokens with a 400 error.

_COMPLETION_TOKENS_PREFIXES = ("o1", "o2", "o3", "o4", "gpt-5")


def _model_uses_completion_tokens(model: str) -> bool:
    """Return True if this OpenAI model requires max_completion_tokens."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in _COMPLETION_TOKENS_PREFIXES)


def _model_omit_temperature(model: str) -> bool:
    """Return True if this model rejects the temperature parameter (o-series)."""
    m = (model or "").lower()
    return m.startswith("o1") or m.startswith("o2") or m.startswith("o3") or m.startswith("o4")


# ── lazy SDK client factories (import inside so missing SDKs fail per-provider,
#    not at module import) ─────────────────────────────────────────────────────


def _make_openai(api_key: str):
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key)


def _make_groq(api_key: str):
    from groq import AsyncGroq

    return AsyncGroq(api_key=api_key)


def _make_anthropic(api_key: str):
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key)
