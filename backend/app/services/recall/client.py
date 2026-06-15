"""
Low-level Recall.ai API client.

Wraps the Recall REST API for bot lifecycle operations:
- Spawn a bot into a meeting
- Check bot status
- Remove a bot from a meeting
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Recall API base URL pattern: https://us-west-2.recall.ai/api/v1/
_DEFAULT_REGION = "us-west-2"


class RecallClient:
    """Thin wrapper around the Recall.ai REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("RECALL_API_KEY", "")
        self.region = region or os.getenv("RECALL_REGION", _DEFAULT_REGION)
        self.base_url = f"https://{self.region}.recall.ai/api/v1"
        self._timeout = httpx.Timeout(30.0, connect=10.0)

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Bot lifecycle
    # ------------------------------------------------------------------

    async def create_bot(
        self,
        meeting_url: str,
        bot_name: str = "Pnyx AI Assistant",
        webhook_url: Optional[str] = None,
        transcription_provider: Optional[str] = None,
        realtime: bool = True,
    ) -> Dict[str, Any]:
        """
        Spawn a Recall bot into a meeting.

        Args:
            meeting_url: The URL of the meeting to join
            bot_name: Display name for the bot
            webhook_url: Optional webhook override
            transcription_provider: Optional provider ID (e.g. 'assembly_ai_v3')
            realtime: Whether to enable real-time transcription (requires provider)
        """
        webhook_url = webhook_url or os.getenv("RECALL_WEBHOOK_URL", "")

        payload: Dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            # Robust lifecycle: never let a bot sit indefinitely knocking on a
            # meeting or lurking in a call it can't record. All values are
            # server-enforced by Recall, so they hold even if our webhooks are
            # missed. Override via env without a code change.
            "automatic_leave": {
                # Not admitted from the waiting room in this many seconds → leave.
                # (Recall default is 1200s/20min — far too long; users find it creepy.)
                "waiting_room_timeout": int(
                    os.getenv("RECALL_WAITING_ROOM_TIMEOUT_SECONDS", "300")
                ),
                # No participant ever joins the call → leave.
                "noone_joined_timeout": int(
                    os.getenv("RECALL_NOONE_JOINED_TIMEOUT_SECONDS", "300")
                ),
                # In the call but unable to record (no permission granted) → leave.
                "in_call_not_recording_timeout": int(
                    os.getenv("RECALL_IN_CALL_NOT_RECORDING_TIMEOUT_SECONDS", "300")
                ),
                # Host explicitly denied recording consent → leave almost immediately.
                "recording_permission_denied_timeout": int(
                    os.getenv("RECALL_RECORDING_PERMISSION_DENIED_TIMEOUT_SECONDS", "30")
                ),
                # Everyone else left and the bot is alone → leave shortly after.
                # activate_after must be >= 1 per Recall validation.
                "everyone_left_timeout": {
                    "timeout": int(
                        os.getenv("RECALL_EVERYONE_LEFT_TIMEOUT_SECONDS", "60")
                    ),
                    "activate_after": 1,
                },
            },
        }

        # Transcription (current Recall API).
        #
        # The OLD `realtime_transcription: {enabled: true}` shape silently sent
        # NO transcript events — that is why transcripts never arrived before.
        # The current API needs `recording_config.transcript.provider`.
        #
        # Two modes, selected by the provider name:
        #   * "*_async"     → post-meeting transcript, fetched after the bot
        #                     finishes. No webhook endpoint needed. Default.
        #   * "*_streaming" → realtime transcripts pushed to our webhook; we add
        #                     a realtime_endpoints entry with a `?token=` so we
        #                     can authenticate it without Recall's signature scheme.
        provider_id = transcription_provider or os.getenv(
            "RECALL_TRANSCRIPT_PROVIDER", "recallai_async"
        )
        is_streaming = "streaming" in provider_id.lower()

        if realtime:
            # Default to auto so Hindi+English (Hinglish) code-switching is
            # detected rather than forced to one language.
            language = os.getenv("RECALL_TRANSCRIPT_LANGUAGE", "auto").strip()
            mode = os.getenv("RECALL_TRANSCRIPT_MODE", "prioritize_low_latency").strip()
            is_multilingual = language.lower() in ("", "auto", "multi")
            pl = provider_id.lower()

            # Each provider expects a different option shape for multilingual.
            provider_opts: Dict[str, Any] = {}
            if "recallai" in pl:
                # Native Recall: language_code="auto" enables language detection.
                lang_code = "auto" if is_multilingual else language
                provider_opts["language_code"] = lang_code
                if is_streaming:
                    # recallai_streaming low-latency mode is ENGLISH-ONLY. For
                    # Hinglish/auto/any non-English we must use accuracy mode or
                    # Recall rejects the bot with a 400.
                    if lang_code.lower() == "en":
                        provider_opts["mode"] = mode
                    else:
                        provider_opts["mode"] = "prioritize_accuracy"
            elif "deepgram" in pl:
                # Deepgram nova-3 + language="multi" handles Hindi/English well.
                provider_opts["model"] = os.getenv("RECALL_DEEPGRAM_MODEL", "nova-3")
                provider_opts["language"] = "multi" if is_multilingual else language
            elif "elevenlabs" in pl:
                # ElevenLabs scribe_v2 auto-detects when language_code is omitted.
                provider_opts["model_id"] = os.getenv("RECALL_ELEVENLABS_MODEL", "scribe_v2")
                if not is_multilingual:
                    provider_opts["language_code"] = language
            else:
                if is_streaming:
                    provider_opts["mode"] = mode
                if not is_multilingual:
                    provider_opts["language_code"] = language

            recording_config: Dict[str, Any] = {
                "transcript": {
                    "provider": {provider_id: provider_opts},
                    "diarization": {"use_separate_streams_when_available": True},
                },
            }

            # Realtime endpoints push transcripts live to our webhook. Only add
            # them when realtime delivery is enabled AND the provider streams.
            # When disabled, we still get the full transcript via the
            # post-meeting fetch in _finalize_bot.
            realtime_enabled = os.getenv("RECALL_REALTIME_ENABLED", "true").lower() == "true"
            if realtime_enabled and is_streaming and webhook_url:
                token = os.getenv("RECALL_WEBHOOK_TOKEN", "").strip()
                endpoint_url = webhook_url
                if token:
                    sep = "&" if "?" in endpoint_url else "?"
                    endpoint_url = f"{endpoint_url}{sep}token={token}"
                recording_config["realtime_endpoints"] = [
                    {
                        "type": "webhook",
                        "url": endpoint_url,
                        "events": ["transcript.data", "transcript.partial_data"],
                    }
                ]

            payload["recording_config"] = recording_config

        logger.info(
            "[RecallClient] Creating bot for %s (name=%s, provider=%s, mode=%s, webhook=%s)",
            meeting_url,
            bot_name,
            provider_id if realtime else "off",
            "streaming" if is_streaming else "async",
            webhook_url[:60] if (is_streaming and webhook_url) else "n/a",
        )

        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            response = await client.post(
                f"{self.base_url}/bot",
                json=payload,
                headers=self._headers,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"[RecallClient] HTTPStatusError during create_bot: {e.response.text}")
                raise
            data = response.json()

        logger.info(
            "[RecallClient] Bot created: recall_bot_id=%s status=%s",
            data.get("id"),
            data.get("status_changes", [{}])[-1].get("code", "unknown")
            if data.get("status_changes")
            else "unknown",
        )
        return data

    async def get_bot_status(self, recall_bot_id: str) -> Dict[str, Any]:
        """Get current status of a Recall bot."""
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            response = await client.get(
                f"{self.base_url}/bot/{recall_bot_id}",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    async def remove_bot(self, recall_bot_id: str) -> Dict[str, Any]:
        """Tell the bot to leave the meeting."""
        logger.info("[RecallClient] Removing bot: recall_bot_id=%s", recall_bot_id)
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            response = await client.post(
                f"{self.base_url}/bot/{recall_bot_id}/leave_call",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_bot_media_urls(self, recall_bot_id: str) -> Dict[str, Any]:
        """
        Return presigned download URLs for the bot's mixed video and audio.

        Current Recall API: bot.recordings[].media_shortcuts.{video_mixed,
        audio_mixed}.data.download_url, available once each media's status is
        'done'. URLs are time-limited (presigned S3).
        """
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        out: Dict[str, Any] = {"video_url": None, "audio_url": None}
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            resp = await client.get(
                f"{self.base_url}/bot/{recall_bot_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            bot = resp.json()

        for rec in bot.get("recordings", []) or []:
            ms = rec.get("media_shortcuts") or {}
            for out_key, media_key in (("video_url", "video_mixed"), ("audio_url", "audio_mixed")):
                media = ms.get(media_key) or {}
                if (media.get("status") or {}).get("code") == "done":
                    url = (media.get("data") or {}).get("download_url")
                    if url and not out[out_key]:
                        out[out_key] = url
        return out

    async def get_bot_transcript(self, recall_bot_id: str) -> Any:
        """
        Fetch the COMPLETE post-meeting transcript for a bot.

        Current Recall API: the bot object exposes the transcript via
        recordings[].media_shortcuts.transcript with a presigned S3 download_url.
        (The old GET /bot/{id}/transcript endpoint 400s.) We only return it once
        the transcript status is 'done', else [] so callers can retry.

        Returns a list of utterances: [{participant:{name}, words:[{text,
        start_timestamp:{relative}, end_timestamp:{relative}}]}].
        """
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            bot_resp = await client.get(
                f"{self.base_url}/bot/{recall_bot_id}",
                headers=self._headers,
            )
            bot_resp.raise_for_status()
            bot = bot_resp.json()

            download_url = None
            for rec in bot.get("recordings", []) or []:
                tr = (rec.get("media_shortcuts") or {}).get("transcript") or {}
                status_code = (tr.get("status") or {}).get("code")
                url = (tr.get("data") or {}).get("download_url")
                if url and status_code == "done":
                    download_url = url
                    break

            if not download_url:
                logger.info(
                    "[RecallClient] Transcript not ready yet for bot %s", recall_bot_id
                )
                return []

            # Presigned S3 URL — must be fetched WITHOUT the Recall auth header.
            dl = await client.get(download_url)
            dl.raise_for_status()
            return dl.json()
