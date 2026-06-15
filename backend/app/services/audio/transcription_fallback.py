"""
Transcription provider fallback wrapper.

Why this exists
---------------
The streaming pipeline picks ONE transcription provider per WebSocket session
(``audio.py``) and never switches. When ElevenLabs returns 401 (key exhausted —
which has happened in production) or 429 (rate limit), the meeting keeps running
but silently stops producing transcript: the manager just logged the error and
forwarded it to the client.

``TranscriptionFallbackClient`` wraps a primary client (ElevenLabs) and a
fallback client (Groq) behind the *same* interface the manager already calls
(``transcribe_audio_async`` / ``transcribe_full_audio`` / ``.provider``), so it
drops in transparently. On a primary failure it switches to the fallback for the
rest of the session, but periodically re-probes the primary so the session
recovers automatically if the primary comes back (e.g. the operator rotates the
key). A user-facing error is only surfaced when *both* providers fail.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Error signatures (substrings, lowercased) that mean "this provider is broken,
# try the other one". Mirrors the detection the manager already used for EL.
_PROVIDER_FAILURE_MARKERS = (
    "401",
    "403",
    "invalid_api_key",
    "invalid api key",
    "rate_limit_exceeded",
    "rate limit",
    "429",
    "quota",
    "resource_exhausted",
    "timeout",
    "timed out",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "overloaded",
    "internal",
)


def _is_provider_failure(result: Any) -> bool:
    """Decide whether a returned transcription dict represents a provider
    failure (as opposed to a normal empty/silence result).

    A result is a failure when it carries an ``error`` whose text matches a
    known provider-failure marker. A plain empty transcript (``text=""`` with no
    error) is NOT a failure — that's just silence.
    """
    if result is None:
        return True
    if not isinstance(result, dict):
        return False
    err = result.get("error")
    if not err:
        return False
    blob = str(err).lower()
    return any(marker in blob for marker in _PROVIDER_FAILURE_MARKERS)


class TranscriptionFallbackClient:
    """Wrap a primary + fallback transcription client with auto-failover.

    Interface-compatible with ``GroqTranscriptionClient`` /
    ``ElevenLabsTranscriptionClient`` so the streaming manager uses it without
    modification.
    """

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        *,
        reprobe_seconds: Optional[float] = None,
        on_fallback: Optional[Callable[[str, str, str], Any]] = None,
        on_recover: Optional[Callable[[str], Any]] = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.reprobe_seconds = (
            reprobe_seconds
            if reprobe_seconds is not None
            else float(os.getenv("TRANSCRIPTION_REPROBE_SECONDS", "180"))
        )
        self._on_fallback = on_fallback
        self._on_recover = on_recover

        self._fallen_back = False
        self._last_reprobe_at = 0.0

    # ── public, interface-compatible surface ─────────────────────────────────

    @property
    def provider(self) -> str:
        """The provider currently serving requests (used by the manager for
        log/error labels)."""
        active = self.fallback if self._fallen_back else self.primary
        return getattr(active, "provider", "transcription")

    @property
    def fell_back(self) -> bool:
        return self._fallen_back

    async def transcribe_audio_async(self, *args, **kwargs) -> dict:
        return await self._dispatch("transcribe_audio_async", *args, **kwargs)

    async def transcribe_full_audio(self, *args, **kwargs) -> dict:
        return await self._dispatch("transcribe_full_audio", *args, **kwargs)

    # ── core dispatch with failover ──────────────────────────────────────────

    async def _dispatch(self, method_name: str, *args, **kwargs) -> dict:
        order = self._provider_order()
        errors: List[str] = []
        last_result: Optional[dict] = None

        for client, is_primary in order:
            method = getattr(client, method_name, None)
            if method is None:
                continue
            pname = getattr(client, "provider", "?")
            try:
                result = await method(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - normalize to a result dict
                errors.append(f"{pname}: {exc}")
                if is_primary and self._fallen_back:
                    self._mark_reprobe_attempt()
                continue

            if is_primary and self._fallen_back:
                self._mark_reprobe_attempt()

            if _is_provider_failure(result):
                errors.append(f"{pname}: {result.get('error')}")
                last_result = result
                continue

            # Success.
            await self._on_success(is_primary, pname)
            return result

        # Both providers failed this chunk — surface a combined error so the
        # manager's on_error path fires (this is the only case the user sees).
        logger.error(
            "[TranscriptionFallback] all providers failed for %s: %s",
            method_name,
            "; ".join(errors),
        )
        if last_result is not None:
            return last_result
        return {
            "text": "",
            "confidence": 0.0,
            "error": "; ".join(errors) or "transcription_failed",
        }

    def _provider_order(self) -> List[Tuple[Any, bool]]:
        """Ordered (client, is_primary) list to try this call."""
        if not self._fallen_back:
            return [(self.primary, True), (self.fallback, False)]
        # Currently on the fallback. Periodically re-probe the primary first so
        # the session can recover; otherwise just use the fallback (with the
        # primary as a last resort if the fallback also dies).
        if self._should_reprobe():
            logger.info("[TranscriptionFallback] re-probing primary '%s'", self.primary_name)
            return [(self.primary, True), (self.fallback, False)]
        return [(self.fallback, False), (self.primary, True)]

    # ── state transitions ────────────────────────────────────────────────────

    async def _on_success(self, is_primary: bool, pname: str) -> None:
        if is_primary:
            if self._fallen_back:
                # Primary recovered — switch back.
                self._fallen_back = False
                logger.warning(
                    "[TranscriptionFallback] primary '%s' RECOVERED — switching back",
                    pname,
                )
                await _maybe_call(self._on_recover, pname)
        else:
            if not self._fallen_back:
                # First fall-back event.
                self._fallen_back = True
                self._mark_reprobe_attempt()
                logger.warning(
                    "[TranscriptionFallback] primary '%s' failed — FELL BACK to '%s'",
                    self.primary_name,
                    pname,
                )
                await _maybe_call(
                    self._on_fallback, self.primary_name, pname, "primary_failed"
                )

    def _should_reprobe(self) -> bool:
        return (time.monotonic() - self._last_reprobe_at) >= self.reprobe_seconds

    def _mark_reprobe_attempt(self) -> None:
        self._last_reprobe_at = time.monotonic()

    @property
    def primary_name(self) -> str:
        return getattr(self.primary, "provider", "primary")

    @property
    def fallback_name(self) -> str:
        return getattr(self.fallback, "provider", "fallback")


async def _maybe_call(cb: Optional[Callable], *args) -> None:
    """Invoke an optional callback that may be sync or async."""
    if cb is None:
        return
    try:
        res = cb(*args)
        if inspect.isawaitable(res):
            await res
    except Exception as e:  # callbacks must never break transcription
        logger.debug("[TranscriptionFallback] callback error: %s", e)
