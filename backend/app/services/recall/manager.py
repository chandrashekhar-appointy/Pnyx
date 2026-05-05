"""
Recall.ai Manager — high-level orchestrator.

Responsibilities:
  - Quota enforcement (5 hr / 7-day rolling window per user)
  - Bot spawn orchestration (create meeting + bot row, call RecallClient)
  - Webhook processing (signature verification, idempotent transcript storage,
    Redis Pub/Sub broadcast, AI Participant ingestion)
  - Bot status transitions and end-of-meeting finalization
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

try:
    from ...db import DatabaseManager
    from .client import RecallClient
except (ImportError, ValueError):
    try:
        from app.db import DatabaseManager
        from app.services.recall.client import RecallClient
    except (ImportError, ValueError):
        from db import DatabaseManager
        from services.recall.client import RecallClient

logger = logging.getLogger(__name__)

# 5 hours expressed in seconds
WEEKLY_QUOTA_SECONDS = 5 * 60 * 60

# Recall status code → internal status mapping
_RECALL_STATUS_MAP: Dict[str, str] = {
    "ready": "requesting",
    "joining_call": "joining",
    "in_waiting_room": "joining",
    "in_call_not_recording": "joining",
    "in_call_recording": "recording",
    "recording_permission_denied": "fatal",
    "recording_done": "completed",
    "done": "completed",
    "fatal": "fatal",
    "call_ended": "completed",
    "media_expired": "completed",
    "analysis_done": "completed",
}


class RecallManager:
    """High-level orchestrator for Recall.ai bot operations."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        recall_client: Optional[RecallClient] = None,
    ):
        self.db = db or DatabaseManager()
        self.client = recall_client or RecallClient()
        self.webhook_secret = os.getenv("RECALL_WEBHOOK_SECRET", "")
        self.ai_engines = {}

        redis_url = (
            os.getenv("REDIS_URL")
            or os.getenv("CELERY_BROKER_URL")
            or "redis://localhost:6379/0"
        )
        
        # In development, fallback to fakeredis if real Redis is missing
        if os.getenv("ENVIRONMENT") == "development" or os.getenv("NODE_ENV") == "development":
            try:
                # Ping test
                import redis
                r = redis.from_url(redis_url)
                r.ping()
                self.redis = aioredis.from_url(redis_url)
            except Exception:
                logger.warning("[RecallManager] Redis not reachable, falling back to fakeredis")
                import fakeredis.aioredis
                self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        else:
            self.redis = aioredis.from_url(redis_url)

    async def remove_bot(self, recall_bot_id: str):
        """Instruct the bot to leave the meeting."""
        logger.info("[RecallManager] Manually removing bot: %s", recall_bot_id)
        try:
            await self.client.remove_bot(recall_bot_id)
        except Exception as e:
            logger.error("[RecallManager] Failed to remove bot %s: %s", recall_bot_id, e)

    # ------------------------------------------------------------------
    # Quota
    # ------------------------------------------------------------------

    async def check_quota(self, user_email: str) -> Dict[str, Any]:
        """
        Check whether the user has remaining bot usage quota.

        Returns:
            { "allowed": bool, "used_seconds": int, "remaining_seconds": int }
        """
        used = await self.db.get_user_bot_usage_seconds(user_email, days=7)
        remaining = max(0, WEEKLY_QUOTA_SECONDS - used)
        return {
            "allowed": remaining > 0,
            "used_seconds": used,
            "remaining_seconds": remaining,
            "quota_seconds": WEEKLY_QUOTA_SECONDS,
        }

    # ------------------------------------------------------------------
    # Spawn
    # ------------------------------------------------------------------

    async def spawn_bot(
        self,
        meeting_id: str,
        meeting_url: str,
        user_email: str,
        bot_name: str = "Pnyx AI Assistant",
    ) -> Dict[str, Any]:
        """
        Spawn a Recall bot into a meeting.

        Steps:
          1. Enforce weekly quota
          2. Check for existing active bot on same meeting
          3. Call RecallClient.create_bot
          4. Insert meeting_bots row
        """
        # 1. Quota check
        quota = await self.check_quota(user_email)
        if not quota["allowed"]:
            logger.warning(
                "[RecallManager] Quota exceeded for %s (used=%ds)",
                user_email,
                quota["used_seconds"],
            )
            return {
                "success": False,
                "error": "weekly_quota_exceeded",
                "message": (
                    f"Weekly bot usage quota exceeded. "
                    f"Used {quota['used_seconds'] // 60}m of "
                    f"{WEEKLY_QUOTA_SECONDS // 3600}h allowed."
                ),
                "quota": quota,
            }

        # 2. Check for existing bot session (strictly one-time join)
        existing = await self.db.get_bot_session_by_meeting(meeting_id)
        if existing:
            logger.info(
                "[RecallManager] Bot session already exists for meeting %s (status=%s). Skipping duplicate spawn.",
                meeting_id,
                existing["status"],
            )
            return {
                "success": False,
                "error": "bot_already_exists",
                "message": "A bot session already exists for this meeting.",
                "bot": existing,
            }

        # 3. Call Recall API
        try:
            # Transcription provider is configured in Recall dashboard
            # (ap-northeast-1 rejects explicit transcription_options in the API payload)
            recall_response = await self.client.create_bot(
                meeting_url=meeting_url,
                bot_name=bot_name,
            )
        except Exception as e:
            logger.error("[RecallManager] Failed to create bot: %s", e, exc_info=True)
            return {
                "success": False,
                "error": "recall_api_error",
                "message": f"Failed to spawn bot: {str(e)}",
            }

        recall_bot_id = recall_response.get("id")
        if not recall_bot_id:
            logger.error("[RecallManager] Recall API returned no bot id: %s", recall_response)
            return {
                "success": False,
                "error": "recall_api_error",
                "message": "Recall API did not return a bot ID.",
            }

        # 4. Persist to DB
        await self.db.create_bot_session(
            meeting_id=meeting_id,
            recall_bot_id=str(recall_bot_id),
            user_email=user_email,
            meeting_url=meeting_url,
            bot_name=bot_name,
        )

        logger.info(
            "[RecallManager] Bot spawned: meeting=%s recall_bot_id=%s user=%s",
            meeting_id,
            recall_bot_id,
            user_email,
        )

        return {
            "success": True,
            "recall_bot_id": str(recall_bot_id),
            "meeting_id": meeting_id,
            "status": "requesting",
            "quota": quota,
        }

    # ------------------------------------------------------------------
    # Remove bot
    # ------------------------------------------------------------------

    async def remove_bot(self, meeting_id: str) -> Dict[str, Any]:
        """Remove bot from a meeting and mark as completed."""
        bot = await self.db.get_bot_session_by_meeting(meeting_id)
        if not bot:
            return {"success": False, "error": "no_bot", "message": "No bot found for this meeting."}

        recall_bot_id = bot["recall_bot_id"]
        try:
            await self.client.remove_bot(recall_bot_id)
        except Exception as e:
            logger.error("[RecallManager] Failed to remove bot: %s", e)
            # Still update local status even if Recall API fails
            pass

        await self._finalize_bot(recall_bot_id, status="completed")

        return {"success": True, "recall_bot_id": recall_bot_id, "status": "completed"}

    # ------------------------------------------------------------------
    # Webhook processing
    # ------------------------------------------------------------------

    def verify_signature(self, raw_body: bytes, signature: str, headers: Dict[str, str] = None) -> bool:
        """Verify Recall.ai webhook HMAC-SHA256 signature."""
        if not self.webhook_secret:
            logger.warning("[RecallManager] No RECALL_WEBHOOK_SECRET configured, skipping verification")
            return True

        headers = headers or {}
        headers_lower = {k.lower(): str(v) for k, v in headers.items()}
        
        # 1. Try Svix-v1 format (Standard Webhooks)
        svix_id = headers_lower.get("svix-id") or headers_lower.get("webhook-id")
        svix_timestamp = headers_lower.get("svix-timestamp") or headers_lower.get("webhook-timestamp")
        svix_signature = headers_lower.get("svix-signature") or headers_lower.get("webhook-signature")
        
        if svix_id and svix_timestamp and svix_signature:
            import base64
            svix_sig_v1 = None
            for sig in svix_signature.split(" "):
                if sig.startswith("v1,"):
                    svix_sig_v1 = sig.split("v1,")[1]
                    break
            
            if svix_sig_v1:
                try:
                    msg = f"{svix_id}.{svix_timestamp}.{raw_body.decode('utf-8')}"
                    secret_str = self.webhook_secret
                    if secret_str.startswith("whsec_"):
                        secret_str = secret_str[6:]
                    
                    secret_bytes = base64.b64decode(secret_str)
                    expected_digest = hmac.new(
                        secret_bytes,
                        msg.encode("utf-8"),
                        hashlib.sha256,
                    ).digest()
                    expected_sig = base64.b64encode(expected_digest).decode("utf-8")
                    
                    if hmac.compare_digest(expected_sig, svix_sig_v1):
                        return True
                except Exception as e:
                    logger.debug("[RecallManager] Failed to verify Svix signature: %s", e)

        # 2. Fallback to basic HMAC on raw_body (Legacy Recall.ai behavior)
        if signature:
            expected = hmac.new(
                self.webhook_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(expected, signature):
                return True

        return False

    async def process_webhook(
        self, payload: Dict[str, Any], raw_body: bytes, signature: str, headers: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Process an incoming Recall.ai webhook event.

        Handles:
          - Transcript data events → store + broadcast
          - Bot status change events → update status + finalize on completion
        """
        # Verify signature
        if not self.verify_signature(raw_body, signature, headers):
            logger.error(
                "[RecallManager] Webhook signature verification failed! "
                "The RECALL_WEBHOOK_SECRET in your .env file does not match "
                "the signature sent by Recall.ai."
            )
            return {"status": "error", "message": "Invalid signature"}

        event_type = payload.get("event", "")
        data = payload.get("data", {})

        logger.info("[RecallManager] Webhook event: %s", event_type)

        if event_type in ("bot.transcription", "transcript.data", "transcript.partial_data"):
            return await self._handle_transcript_event(data, event_type)
        elif event_type in ("bot.status_change", "bot_status_change"):
            return await self._handle_status_change(data)
        elif event_type.startswith("bot."):
            # Modern Recall.ai top-level events (e.g. bot.done, bot.fatal, bot.joining_call)
            recall_status = event_type.split(".", 1)[1]
            
            # Ensure bot_id exists safely
            if "bot_id" not in data:
                bot_id = payload.get("bot_id") or data.get("id") or ""
                data["bot_id"] = bot_id
            
            if "code" not in data and "status" not in data:
                data["code"] = recall_status

            return await self._handle_status_change(data)
        else:
            logger.debug("[RecallManager] Unhandled webhook event: %s", event_type)
            return {"status": "ignored", "event": event_type}

    async def _handle_transcript_event(
        self, data: Dict[str, Any], event_type: str
    ) -> Dict[str, Any]:
        """Process a transcript webhook event."""
        bot_id = data.get("bot_id") or data.get("bot", {}).get("id", "")
        transcript_data = data.get("transcript", data)

        words = transcript_data.get("words", [])
        text = " ".join(w.get("text", "") for w in words).strip()
        if not text:
            # Some events have text directly
            text = (transcript_data.get("text") or "").strip()

        speaker = transcript_data.get("speaker") or transcript_data.get("speaker_name") or "Unknown"
        is_final = transcript_data.get("is_final", event_type == "transcript.data")
        segment_index = data.get("sequence_id") or data.get("segment_id") or 0
        start_time = transcript_data.get("start_time") or transcript_data.get("start_ts")
        end_time = transcript_data.get("end_time") or transcript_data.get("end_ts")

        if not text:
            return {"status": "skipped", "reason": "empty_text"}

        # Look up bot session
        bot_session = await self.db.get_bot_session(str(bot_id)) if bot_id else None
        if not bot_session:
            logger.warning("[RecallManager] No bot session for recall_bot_id=%s", bot_id)
            return {"status": "error", "message": "Unknown bot ID"}

        meeting_id = bot_session["meeting_id"]

        # Idempotent insert: recall_bot_id + segment_index
        if is_final:
            try:
                await self.db.save_meeting_transcript(
                    meeting_id=meeting_id,
                    transcript=text,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    audio_start_time=float(start_time) if start_time else None,
                    audio_end_time=float(end_time) if end_time else None,
                    source="recall_bot",
                    speaker=str(speaker),
                )
            except Exception as e:
                logger.error("[RecallManager] Failed to save transcript: %s", e)

        # Broadcast via Redis Pub/Sub
        channel = f"meeting:{meeting_id}:transcript"
        broadcast_payload = {
            "type": "partial" if not is_final else "final",
            "text": text,
            "speaker": speaker,
            "is_final": is_final,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "recall_bot",
            "audio_start_time": start_time,
            "audio_end_time": end_time,
        }

        try:
            await self.redis.publish(channel, json.dumps(broadcast_payload))
        except Exception as e:
            logger.error("[RecallManager] Redis publish failed: %s", e)

        # -------------------------
        # AI Participant Evaluation
        # -------------------------
        if meeting_id not in self.ai_engines:
            user_email = bot_session.get("user_email") or "bot@system"
            try:
                from app.api.routers.audio import _build_ai_meeting_context
                ctx = await _build_ai_meeting_context(meeting_id, user_email)
            except Exception as e:
                logger.warning("[RecallManager] Failed to build ai context: %s", e)
                from app.services.ai_participant import MeetingContext
                ctx = MeetingContext(meeting_id=meeting_id)

            from app.services.ai_participant import AIParticipantEngine
            engine = AIParticipantEngine(
                db=self.db,
                user_email=user_email,
                meeting_context=ctx
            )
            try:
                await engine.load_host_state(meeting_id)
            except Exception as e:
                logger.debug("[RecallManager] Failed to load ai host state: %s", e)
            self.ai_engines[meeting_id] = engine

        engine = self.ai_engines[meeting_id]
        
        try:
            host_payload = await engine.ingest_transcript_host(
                text=text,
                transcript_time_seconds=float(end_time) if end_time else None,
            )
            
            suggestions = host_payload.get("suggestions") or []
            interventions = host_payload.get("interventions") or []
            state_delta = host_payload.get("state_delta") or {}

            # Publish suggestions
            for suggestion in suggestions:
                payload = dict(suggestion)
                payload["type"] = "ai_host_suggestion"
                await self.redis.publish(f"meeting:{meeting_id}:ai_host_suggestion", json.dumps(payload))

            # Publish interventions
            for intervention in interventions:
                payload = dict(intervention)
                payload["type"] = "ai_host_intervention"
                await self.redis.publish(f"meeting:{meeting_id}:ai_host_intervention", json.dumps(payload))

            # Publish state delta
            if state_delta:
                payload = {
                    "type": "ai_host_state_delta",
                    "state": state_delta,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self.redis.publish(f"meeting:{meeting_id}:ai_host_state_delta", json.dumps(payload))

        except Exception as e:
            logger.error("[RecallManager] AI ingestion error for %s: %s", meeting_id, e)

        status_mark = "[FINAL]" if is_final else "[PARTIAL]"
        logger.info(
            "🎤 %s %s: %s (meeting=%s)",
            status_mark,
            speaker,
            text[:120],
            meeting_id,
        )

        return {"status": "processed", "is_final": is_final, "meeting_id": meeting_id}

    async def _handle_status_change(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a bot status change event."""
        bot_id = data.get("bot_id") or data.get("bot", {}).get("id", "")
        status_data = data.get("status", {})
        recall_status = status_data.get("code") or data.get("code", "")

        internal_status = _RECALL_STATUS_MAP.get(recall_status, "")
        if not internal_status:
            logger.debug(
                "[RecallManager] Unknown recall status: %s",
                recall_status,
            )
            return {"status": "ignored", "recall_status": recall_status}

        logger.info(
            "[RecallManager] Bot status change: recall_bot_id=%s recall_status=%s → %s",
            bot_id,
            recall_status,
            internal_status,
        )

        # Update DB
        error_msg = status_data.get("message") if internal_status == "fatal" else None
        await self.db.update_bot_status(
            recall_bot_id=str(bot_id),
            status=internal_status,
            error_message=error_msg,
        )

        # Broadcast status update
        bot_session = await self.db.get_bot_session(str(bot_id)) if bot_id else None
        if bot_session:
            meeting_id = bot_session["meeting_id"]
            channel = f"meeting:{meeting_id}:bot_status"
            try:
                await self.redis.publish(
                    channel,
                    json.dumps({
                        "type": "bot_status",
                        "status": internal_status,
                        "recall_status": recall_status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }),
                )
            except Exception as e:
                logger.error("[RecallManager] Redis status publish failed: %s", e)

        # Finalize on completion
        if internal_status in ("completed", "fatal"):
            await self._finalize_bot(str(bot_id), status=internal_status)

        return {"status": "processed", "internal_status": internal_status}

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    async def _fetch_and_store_recall_transcript(
        self, recall_bot_id: str, meeting_id: str
    ) -> int:
        """
        Fetch the post-meeting transcript from Recall.ai's API and store it.

        Returns the number of segments stored.
        """
        try:
            transcript_data = await self.client.get_bot_transcript(recall_bot_id)
        except Exception as e:
            logger.warning(
                "[RecallManager] Failed to fetch transcript from Recall API for bot %s: %s",
                recall_bot_id, e,
            )
            return 0

        # Recall returns a list of transcript segments
        segments = []
        if isinstance(transcript_data, list):
            segments = transcript_data
        elif isinstance(transcript_data, dict):
            segments = transcript_data.get("results", []) or transcript_data.get("data", []) or []
            # Some responses wrap in a single-item list
            if not segments and transcript_data.get("words"):
                segments = [transcript_data]

        if not segments:
            logger.info(
                "[RecallManager] Recall API returned no transcript segments for bot %s",
                recall_bot_id,
            )
            return 0

        stored_count = 0
        for seg in segments:
            # Extract text from words array or direct text field
            words = seg.get("words", [])
            if words:
                text = " ".join(w.get("text", "") for w in words).strip()
            else:
                text = (seg.get("text") or "").strip()

            if not text:
                continue

            speaker = (
                seg.get("speaker")
                or seg.get("speaker_name")
                or (seg.get("participant", {}) or {}).get("name")
                or "Unknown"
            )
            start_time = seg.get("start_time") or seg.get("start_ts")
            end_time = seg.get("end_time") or seg.get("end_ts")

            try:
                await self.db.save_meeting_transcript(
                    meeting_id=meeting_id,
                    transcript=text,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    audio_start_time=float(start_time) if start_time else None,
                    audio_end_time=float(end_time) if end_time else None,
                    source="recall_bot",
                    speaker=str(speaker),
                )
                stored_count += 1
            except Exception as e:
                logger.error("[RecallManager] Failed to store transcript segment: %s", e)

        logger.info(
            "[RecallManager] Fetched and stored %d transcript segments from Recall API for meeting %s",
            stored_count, meeting_id,
        )
        return stored_count

    async def _finalize_bot(self, recall_bot_id: str, status: str = "completed"):
        """
        End-of-meeting finalization:
          1. Calculate duration
          2. Update bot status to completed/fatal
          3. Fetch post-meeting transcript from Recall API if needed
          4. Trigger notes generation (never delete completed meetings)
        """
        bot = await self.db.get_bot_session(recall_bot_id)
        if not bot:
            return

        meeting_id = bot["meeting_id"]
        duration = await self.db.get_meeting_audio_duration_seconds(meeting_id)

        # Update bot status and duration in DB
        await self.db.update_bot_status(
            recall_bot_id=recall_bot_id,
            status=status,
            duration_seconds=duration,
        )
        logger.info(
            "[RecallManager] Bot finalized: recall_bot_id=%s meeting=%s status=%s duration=%ds",
            recall_bot_id,
            meeting_id,
            status,
            duration,
        )

        if meeting_id in self.ai_engines:
            del self.ai_engines[meeting_id]

        # --- Fatal bots: clean up only if truly empty ---
        if status == "fatal":
            try:
                has_segments = await self.db.has_transcript_segments(meeting_id)
                if not has_segments:
                    logger.info(
                        "[RecallManager] Meeting %s is fatal with no transcript. Deleting.",
                        meeting_id,
                    )
                    await self.db.delete_meeting(meeting_id)
                    return
            except Exception as e:
                logger.error("[RecallManager] Failed to check transcript for %s: %s", meeting_id, e)
            return  # Fatal meetings don't generate notes

        # --- Completed bots: always try to produce notes ---
        if status == "completed":
            # Check if real-time transcription already captured segments
            has_segments = False
            try:
                has_segments = await self.db.has_transcript_segments(meeting_id)
            except Exception as e:
                logger.error("[RecallManager] Failed to check transcript for %s: %s", meeting_id, e)

            # If no real-time transcripts, fetch from Recall's post-meeting API
            if not has_segments:
                logger.info(
                    "[RecallManager] No real-time transcripts for meeting %s. "
                    "Fetching post-meeting transcript from Recall API...",
                    meeting_id,
                )
                stored = await self._fetch_and_store_recall_transcript(
                    recall_bot_id, meeting_id
                )
                has_segments = stored > 0

            # If we still have no transcript at all, keep the meeting but log it
            if not has_segments:
                logger.warning(
                    "[RecallManager] Meeting %s completed but no transcript available "
                    "(real-time or post-meeting). Keeping meeting record.",
                    meeting_id,
                )

            # Always trigger notes generation for completed meetings
            try:
                try:
                    from celery_app import celery_app
                except ImportError:
                    from app.celery_app import celery_app

                celery_app.send_task(
                    "tasks.generate_notes.generate_meeting_notes_task",
                    kwargs={
                        "meeting_id": meeting_id,
                        "user_email": bot["user_email"],
                        "source": "recall_bot",
                    },
                )
                logger.info(
                    "[RecallManager] Notes generation triggered for meeting %s",
                    meeting_id,
                )
            except Exception as e:
                logger.warning(
                    "[RecallManager] Failed to trigger notes generation: %s", e
                )

    # ------------------------------------------------------------------
    # Status query
    # ------------------------------------------------------------------

    async def get_bot_status(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        """Get the current bot status for a meeting."""
        bot = await self.db.get_bot_session_by_meeting(meeting_id)
        if not bot:
            return None

        return {
            "recall_bot_id": bot["recall_bot_id"],
            "status": bot["status"],
            "bot_name": bot.get("bot_name", "Pnyx AI Assistant"),
            "meeting_url": bot.get("meeting_url", ""),
            "duration_seconds": bot.get("duration_seconds", 0),
            "error_message": bot.get("error_message"),
            "created_at": bot["created_at"].isoformat() if bot.get("created_at") else None,
        }
