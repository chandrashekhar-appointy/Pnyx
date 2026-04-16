import asyncio
import logging
import os
from celery import shared_task
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from ..db import DatabaseManager
    from ..api.routers.transcripts import (
        _resolve_notes_transcript,
        generate_notes_with_gemini_background,
    )
except (ImportError, ValueError):
    from db import DatabaseManager
    from api.routers.transcripts import (
        _resolve_notes_transcript,
        generate_notes_with_gemini_background,
    )


@shared_task(
    bind=True,
    name="tasks.generate_notes.generate_meeting_notes_task",
    autoretry_for=(Exception,),
    retry_backoff=30,  # Start with 30s
    retry_jitter=True,
    max_retries=5,  # Allow up to ~5-10 mins of total delay
)
def generate_meeting_notes_task(
    self,
    meeting_id: str,
    user_email: str,
    source: str = "recall_bot",
    template_id: str = "standard_meeting",
    custom_context: str = "",
):
    """
    Celery task to generate meeting notes.
    Reuses the logic from transcripts.py but runs in the background.
    """
    return asyncio.run(
        _generate_meeting_notes_async(
            self,
            meeting_id=meeting_id,
            user_email=user_email,
            source=source,
            template_id=template_id,
            custom_context=custom_context,
        )
    )


async def _generate_meeting_notes_async(
    task_self,
    meeting_id: str,
    user_email: str,
    source: str,
    template_id: str,
    custom_context: str,
):
    import logging

    logger = logging.getLogger(__name__)
    db = DatabaseManager()
    logger.info(
        f"[Task:GenerateNotes] Starting for meeting={meeting_id} user={user_email}"
    )

    try:
        # 1. Resolve transcript
        full_transcript_text, transcript_source, _ = await _resolve_notes_transcript(
            meeting_id=meeting_id, prefer_diarized=True, explicit_transcript=""
        )

        # 2. Hybrid Recovery for Recall Bots
        # If transcript is empty and this is a bot meeting, try to fetch from Recall API
        if not full_transcript_text.strip() and source == "recall_bot":
            logger.info(
                f"[Task:GenerateNotes] Empty transcript for bot meeting {meeting_id}. Attempting Recall API recovery..."
            )
            try:
                from services.recall.manager import RecallManager
            except ImportError:
                from app.services.recall.manager import RecallManager

            recall_manager = RecallManager(db)
            bot = await db.get_bot_session_by_meeting(meeting_id)
            if bot and bot.get("recall_bot_id"):
                stored_count = await recall_manager._fetch_and_store_recall_transcript(
                    bot["recall_bot_id"], meeting_id
                )
                if stored_count > 0:
                    # Re-resolve now that we've stored segments
                    (
                        full_transcript_text,
                        transcript_source,
                        _,
                    ) = await _resolve_notes_transcript(
                        meeting_id=meeting_id,
                        prefer_diarized=True,
                        explicit_transcript="",
                    )

            if not full_transcript_text.strip():
                # Still empty? Target for retry.
                # Recall might still be processing the 'done' state.
                retries = task_self.request.retries
                max_retries = task_self.max_retries
                if retries < max_retries:
                    logger.info(
                        f"[Task:GenerateNotes] Transcript still empty for {meeting_id} (Attempt {retries + 1}/{max_retries}). Retrying..."
                    )
                    raise Exception(
                        f"Transcript not ready for bot meeting {meeting_id}"
                    )
                else:
                    logger.warning(
                        f"[Task:GenerateNotes] Max retries reached for {meeting_id}. Skipping."
                    )
                    return {
                        "status": "skipped",
                        "reason": "empty_transcript_after_retries",
                    }

        if not full_transcript_text.strip():
            logger.warning(
                f"[Task:GenerateNotes] Empty transcript for meeting={meeting_id}. Skipping."
            )
            return {"status": "skipped", "reason": "empty_transcript"}

        # 3. Get meeting title
        meeting_data = await db.get_meeting(meeting_id)
        meeting_title = (meeting_data or {}).get("title") or "Bot Meeting"

        # 4. Trigger Gemini generation logic
        try:
            from api.routers.transcripts import generate_notes_with_gemini_background
        except ImportError:
            from app.api.routers.transcripts import (
                generate_notes_with_gemini_background,
            )

        await generate_notes_with_gemini_background(
            meeting_id=meeting_id,
            full_transcript_text=full_transcript_text,
            transcript_source=transcript_source,
            template_id=template_id,
            meeting_title=meeting_title,
            custom_context=custom_context,
            user_email=user_email,
        )

        logger.info(
            f"[Task:GenerateNotes] Successfully completed for meeting={meeting_id}"
        )
        return {"status": "completed", "meeting_id": meeting_id}

    except Exception as e:
        # If it's a "Transcript not ready" exception, Celery will retry automatically
        # due to autoretry_for=(Exception,)
        logger.error(f"[Task:GenerateNotes] Error for meeting={meeting_id}: {e}")
        raise
