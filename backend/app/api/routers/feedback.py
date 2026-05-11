from fastapi import APIRouter, Depends, HTTPException
from typing import List
import os
import uuid
import logging

try:
    from ..deps import get_current_user
    from ...schemas.user import User
    from ...schemas.feedback import (
        FeedbackCreate,
        FeedbackResponse,
        FeedbackUpdateStatus,
    )
    from ...db import DatabaseManager
except (ImportError, ValueError):
    from api.deps import get_current_user
    from schemas.user import User
    from schemas.feedback import (
        FeedbackCreate,
        FeedbackResponse,
        FeedbackUpdateStatus,
    )
    from db import DatabaseManager

router = APIRouter()
logger = logging.getLogger(__name__)
db = DatabaseManager()

# Get Admin Emails from Env
ADMIN_EMAILS = [
    email.strip() for email in os.getenv("ADMIN_EMAILS", "").split(",") if email.strip()
]


def is_admin(user_email: str) -> bool:
    return user_email in ADMIN_EMAILS


@router.post("/", response_model=dict)
async def create_feedback(
    data: FeedbackCreate, current_user: User = Depends(get_current_user)
):
    """Submit new feedback"""
    try:
        feedback_id = str(uuid.uuid4())
        await db.create_feedback(
            feedback_id=feedback_id,
            user_id=current_user.email,
            user_email=current_user.email,
            type=data.type,
            title=data.title,
            description=data.description or "",
        )
        return {"message": "Feedback submitted successfully", "id": feedback_id}
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit feedback")


@router.get("/", response_model=List[FeedbackResponse])
async def list_feedback(current_user: User = Depends(get_current_user)):
    """List feedback (Admin sees all, User sees own)"""
    try:
        user_filter = None if is_admin(current_user.email) else current_user.email
        feedback_list = await db.get_feedback(user_filter)
        return feedback_list
    except Exception as e:
        logger.error(f"Error listing feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch feedback")


@router.patch("/{feedback_id}/status")
async def update_feedback_status(
    feedback_id: str,
    data: FeedbackUpdateStatus,
    current_user: User = Depends(get_current_user),
):
    """Update feedback status (Admin only)"""
    if not is_admin(current_user.email):
        raise HTTPException(status_code=403, detail="Only admins can update status")

    try:
        await db.update_feedback_status(feedback_id, data.status)
        return {"message": "Status updated successfully"}
    except Exception as e:
        logger.error(f"Error updating status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update status")


@router.get("/check-admin")
async def check_admin_status(current_user: User = Depends(get_current_user)):
    """Check if current user is admin"""
    return {"is_admin": is_admin(current_user.email)}
