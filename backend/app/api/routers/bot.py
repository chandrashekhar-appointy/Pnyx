"""
Bot API Router — Recall.ai bot management endpoints.

Endpoints:
  POST   /api/meetings/{meeting_id}/invite-bot  — Spawn bot with meeting URL
  DELETE /api/meetings/{meeting_id}/bot          — Remove bot from meeting
  GET    /api/meetings/{meeting_id}/bot-status   — Get current bot status
  GET    /api/bot/quota                          — Get user bot usage quota
  POST   /api/bot/webhook                        — Receive Recall.ai webhook events
  GET    /api/bot/webhook                        — Health check for webhook endpoint
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

try:
    from ..deps import get_current_user
    from ...services.recall.manager import RecallManager
    from ...schemas.user import User
except (ImportError, ValueError):
    try:
        from app.api.deps import get_current_user
        from app.services.recall.manager import RecallManager
        from app.schemas.user import User
    except (ImportError, ValueError):
        from api.deps import get_current_user
        from services.recall.manager import RecallManager
        from schemas.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Bot"])


# Request/Response models
class InviteBotRequest(BaseModel):
    meeting_url: str
    bot_name: str = "Pnyx AI Assistant"


class InviteBotResponse(BaseModel):
    success: bool
    recall_bot_id: Optional[str] = None
    meeting_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


# Shared instances
_recall_manager: Optional[RecallManager] = None


def _get_manager() -> RecallManager:
    global _recall_manager
    if _recall_manager is None:
        _recall_manager = RecallManager()
    return _recall_manager


# ------------------------------------------------------------------
# Bot lifecycle endpoints (authenticated via Depends)
# ------------------------------------------------------------------


@router.post(
    "/meetings/{meeting_id}/invite-bot",
    response_model=InviteBotResponse,
)
async def invite_bot(
    meeting_id: str,
    body: InviteBotRequest,
    current_user: User = Depends(get_current_user),
):
    """Spawn a Recall.ai bot into a meeting."""
    manager = _get_manager()
    result = await manager.spawn_bot(
        meeting_id=meeting_id,
        meeting_url=body.meeting_url,
        user_email=current_user.email,
        bot_name=body.bot_name,
    )

    if not result.get("success"):
        status_code = 429 if result.get("error") == "weekly_quota_exceeded" else 400
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": result.get("error"),
                "message": result.get("message"),
            },
        )

    return InviteBotResponse(
        success=True,
        recall_bot_id=result.get("recall_bot_id"),
        meeting_id=meeting_id,
        status=result.get("status"),
    )


@router.delete("/meetings/{meeting_id}/bot")
async def remove_bot(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
):
    """Remove the active bot from a meeting."""
    manager = _get_manager()
    result = await manager.remove_bot(meeting_id)

    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "No bot found"))

    return result


@router.get("/meetings/{meeting_id}/bot-status")
async def bot_status(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get current bot status for a meeting."""
    manager = _get_manager()
    status = await manager.get_bot_status(meeting_id)

    if not status:
        return {"status": "none", "message": "No bot session for this meeting"}

    return status


@router.get("/meetings/{meeting_id}/bot-recording-url")
async def bot_recording_url(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return presigned video/audio download URLs for an online (bot) meeting.

    Recall stores the recording, so we fetch fresh time-limited URLs on demand.
    """
    manager = _get_manager()
    bot = await manager.db.get_bot_session_by_meeting(meeting_id)
    if not bot:
        raise HTTPException(status_code=404, detail="No bot recording for this meeting")
    if bot.get("user_email") != current_user.email:
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        urls = await manager.client.get_bot_media_urls(bot["recall_bot_id"])
    except Exception as e:
        logger.error("[BotRecordingURL] Failed to fetch media for %s: %s", meeting_id, e)
        raise HTTPException(status_code=502, detail="Could not fetch recording from Recall")

    if not urls.get("video_url") and not urls.get("audio_url"):
        raise HTTPException(status_code=404, detail="Recording not ready yet")

    return {
        "video_url": urls.get("video_url"),
        "audio_url": urls.get("audio_url"),
        "video_filename": f"meeting-{meeting_id}.mp4",
        "audio_filename": f"meeting-{meeting_id}.mp3",
        "expires_in": 3600,
    }


@router.get("/bot/quota")
async def bot_quota(current_user: User = Depends(get_current_user)):
    """Get the current user's bot usage quota."""
    manager = _get_manager()
    quota = await manager.check_quota(current_user.email)
    return quota


@router.get("/meetings/active-bot-sessions")
async def active_bot_sessions(current_user: User = Depends(get_current_user)):
    """Return all currently active bot sessions for the authenticated user."""
    manager = _get_manager()
    sessions = await manager.db.get_active_bot_sessions_for_user(current_user.email)
    # Serialize datetime objects
    results = []
    for s in sessions:
        item = dict(s)
        for key in ("created_at", "updated_at"):
            if item.get(key) and hasattr(item[key], "isoformat"):
                item[key] = item[key].isoformat()
        results.append(item)
    return results


# ------------------------------------------------------------------
# Webhook endpoint (public, HMAC-verified)
# ------------------------------------------------------------------


@router.get("/bot/webhook")
async def test_webhook():
    """Health check for webhook endpoint reachability."""
    return {"status": "ok", "message": "Webhook endpoint is reachable"}


@router.post("/bot/webhook")
async def bot_webhook(request: Request):
    """
    Receives real-time transcript and status webhooks from Recall.ai.

    Two auth paths:
      - Status-change webhooks (dashboard Svix) → HMAC signature verification.
      - Realtime transcript webhooks → `?token=` query param matching
        RECALL_WEBHOOK_TOKEN, since Recall's realtime endpoints don't use the
        same Svix signing.
    """
    import os

    raw_body = await request.body()
    signature = request.headers.get("X-Recall-Signature", "")

    try:
        import json

        payload = json.loads(raw_body)
    except Exception:
        logger.error("[BotWebhook] Failed to parse webhook body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Realtime transcript path: authenticate via the shared token.
    expected_token = os.getenv("RECALL_WEBHOOK_TOKEN", "").strip()
    provided_token = request.query_params.get("token", "").strip()
    pre_verified = bool(expected_token) and provided_token == expected_token

    logger.info(
        "📥 Webhook received: event=%s auth=%s from=%s",
        payload.get("event", "unknown"),
        "token" if pre_verified else "signature",
        request.client.host if request.client else "unknown",
    )

    manager = _get_manager()
    result = await manager.process_webhook(
        payload=payload,
        raw_body=raw_body,
        signature=signature,
        headers=dict(request.headers),
        pre_verified=pre_verified,
    )

    if result.get("status") == "error":
        raise HTTPException(
            status_code=401, detail=result.get("message", "Webhook error")
        )

    return {"status": "success", **result}
