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
        }

        # ap-northeast-1 (and some other regions) reject explicit
        # 'transcription_options'. The provider must be configured in the
        # Recall dashboard instead. We only send 'realtime_transcription'.
        if realtime:
            payload["realtime_transcription"] = {"enabled": True}

        logger.info(
            "[RecallClient] Creating bot for %s (name=%s, realtime=%s, webhook=%s)",
            meeting_url,
            bot_name,
            realtime,
            webhook_url[:60] if webhook_url else "NONE",
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
                f"{self.base_url}/bot/{recall_bot_id}/leave",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_bot_transcript(self, recall_bot_id: str) -> Dict[str, Any]:
        """Fetch the full transcript for a completed bot session."""
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            response = await client.get(
                f"{self.base_url}/bot/{recall_bot_id}/transcript",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
