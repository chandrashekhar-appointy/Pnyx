from fastapi import APIRouter, HTTPException, Depends
import logging
import asyncio

try:
    from ...db import DatabaseManager
    from ...services.credit_manager import CreditManager
    from ..deps import get_admin_user
    from ...schemas.credits import (
        AdminCreditOverrideRequest,
        AdminSetUnlimitedRequest,
        CreditBalanceResponse,
    )
    from ...schemas.user import User
except (ImportError, ValueError):
    from db import DatabaseManager
    from services.credit_manager import CreditManager
    from api.deps import get_admin_user
    from schemas.credits import (
        AdminCreditOverrideRequest,
        AdminSetUnlimitedRequest,
        CreditBalanceResponse,
    )
    from schemas.user import User

router = APIRouter()
logger = logging.getLogger(__name__)
db = DatabaseManager()
credit_mgr = CreditManager(db)


@router.post("/admin/reindex-all")
async def reindex_all(admin: User = Depends(get_admin_user)):
    """Admin endpoint to re-index all past meetings into ChromaDB."""
    debug_logs = []
    try:
        try:
            import sentence_transformers  # noqa: F401

            debug_logs.append("sentence_transformers imported successfully")
        except ImportError:
            logger.error("sentence-transformers not installed")
            raise HTTPException(
                status_code=500,
                detail="sentence-transformers library is missing on the server",
            )

        # Import vector store here to avoid circular imports or early init
        try:
            from app.vector_store import store_meeting_embeddings
        except ImportError:
            try:
                from ...vector_store import store_meeting_embeddings
            except (ImportError, ValueError):
                # Fallback if vector_store.py is in root
                import sys

                sys.path.append(".")
                from vector_store import store_meeting_embeddings

        # 1. Fetch all meetings
        meetings = await db.get_all_meetings()
        debug_logs.append(f"Fetched {len(meetings)} meetings from DB")
        logger.info(f"Re-indexing {len(meetings)} meetings...")

        count = 0
        successful = 0
        failed = 0
        skipped = 0
        errors = []

        for m in meetings:
            meeting_id = m["id"]

            try:
                # 2. Get full details including transcripts
                meeting_data = await db.get_meeting(meeting_id)
                transcripts = []

                if meeting_data and meeting_data.get("transcripts"):
                    transcripts = meeting_data.get("transcripts")
                    debug_logs.append(
                        f"Meeting {meeting_id}: Found {len(transcripts)} transcripts in structure"
                    )
                else:
                    # Fallback to full_transcripts table
                    full_text = await db.get_full_transcript_text(meeting_id)
                    if full_text:
                        logger.info(f"Using fallback full transcript for {meeting_id}")
                        transcripts = [
                            {
                                "text": full_text,
                                "timestamp": meeting_data.get("created_at")
                                if meeting_data
                                else "",
                                "id": meeting_id,
                            }
                        ]
                        debug_logs.append(
                            f"Meeting {meeting_id}: Found full transcript text"
                        )
                    else:
                        debug_logs.append(f"Meeting {meeting_id}: No transcripts found")

                if not transcripts:
                    logger.info(f"Skipping {meeting_id}: no transcripts")
                    skipped += 1
                    continue

                # 3. Store in vector DB (sequential processing to avoid ChromaDB race conditions)
                num_chunks = await store_meeting_embeddings(
                    meeting_id=meeting_id,
                    meeting_title=meeting_data.get("title", "Untitled")
                    if meeting_data
                    else m["title"],
                    meeting_date=meeting_data.get("created_at", "")
                    if meeting_data
                    else m.get("created_at", ""),
                    transcripts=transcripts,
                )

                debug_logs.append(f"Meeting {meeting_id}: Stored {num_chunks} chunks")

                if num_chunks > 0:
                    successful += 1
                    logger.info(f"✅ Indexed meeting {meeting_id}: {num_chunks} chunks")
                else:
                    logger.warning(
                        f"⚠️ Meeting {meeting_id} indexed but produced 0 chunks"
                    )

                count += 1

                # Small delay to ensure ChromaDB processes fully before next meeting
                await asyncio.sleep(0.05)

            except Exception as e:
                failed += 1
                error_msg = f"Failed to index {meeting_id}: {str(e)}"
                logger.error(f"❌ {error_msg}")
                errors.append(error_msg)
                debug_logs.append(f"ERROR {meeting_id}: {str(e)}")
                continue

        return {
            "status": "success",
            "indexed_count": count,
            "successful": successful,
            "failed": failed,
            "skipped": skipped,
            "total_meetings": len(meetings),
            "errors": errors,
            "debug_logs": debug_logs,
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Re-indexing failed: {e}")
        return {"status": "error", "error": str(e), "debug_logs": debug_logs}


# ── Credit Management Admin Endpoints ───────────────────────────────


@router.post("/admin/credits/override")
async def override_credits(
    req: AdminCreditOverrideRequest,
    admin: User = Depends(get_admin_user),
):
    """Add or remove admin bonus credits for a user with audit trail."""
    try:
        balance = await credit_mgr.add_admin_credits(
            user_email=req.user_email,
            amount=req.credits,
            reason=req.reason,
            admin_email=admin.email,
        )
        return {
            "status": "success",
            "user_email": req.user_email,
            "credits_added": req.credits,
            "reason": req.reason,
            "new_balance": balance,
        }
    except Exception as e:
        logger.error(f"Credit override failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/credits/{user_email}", response_model=CreditBalanceResponse)
async def get_user_credits(
    user_email: str,
    admin: User = Depends(get_admin_user),
):
    """View a specific user's credit balance (admin only)."""
    balance = await credit_mgr.get_balance(user_email)
    return CreditBalanceResponse(**balance)


@router.post("/admin/credits/set-unlimited")
async def set_user_unlimited(
    req: AdminSetUnlimitedRequest,
    admin: User = Depends(get_admin_user),
):
    """Toggle unlimited mode for a user."""
    await credit_mgr.set_unlimited(req.user_email, req.is_unlimited)
    return {
        "status": "success",
        "user_email": req.user_email,
        "is_unlimited": req.is_unlimited,
        "set_by": admin.email,
    }
