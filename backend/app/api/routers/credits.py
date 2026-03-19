"""
Credit API Routes — User-facing endpoints for credit balance, history, and purchases.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ...services.credit_manager import CreditManager
    from ...services.razorpay_client import RazorpayClient
    from ...db import DatabaseManager
    from ..deps import get_current_user
    from ...schemas.credits import (
        CreditBalanceResponse,
        CreditHistoryResponse,
        CreditLedgerEntry,
        CreditPurchaseRequest,
        CreditPurchaseResponse,
    )
    from ...schemas.user import User
except (ImportError, ValueError):
    from services.credit_manager import CreditManager
    from services.razorpay_client import RazorpayClient
    from db import DatabaseManager
    from api.deps import get_current_user
    from schemas.credits import (
        CreditBalanceResponse,
        CreditHistoryResponse,
        CreditLedgerEntry,
        CreditPurchaseRequest,
        CreditPurchaseResponse,
    )
    from schemas.user import User

router = APIRouter(prefix="/api/credits", tags=["Credits"])
logger = logging.getLogger(__name__)

db = DatabaseManager()
credit_mgr = CreditManager(db)
razorpay = RazorpayClient()


@router.get("", response_model=CreditBalanceResponse)
async def get_credit_balance(user: User = Depends(get_current_user)):
    """Get the current user's credit balance."""
    balance = await credit_mgr.get_balance(user.email)
    return CreditBalanceResponse(**balance)


@router.get("/history", response_model=CreditHistoryResponse)
async def get_credit_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    """Get the current user's credit transaction history."""
    entries = await credit_mgr.ledger.get_history(
        user_email=user.email, limit=limit, offset=offset
    )
    return CreditHistoryResponse(
        entries=[CreditLedgerEntry(**e) for e in entries]
    )


@router.post("/purchase", response_model=CreditPurchaseResponse)
async def purchase_credits(
    req: CreditPurchaseRequest,
    user: User = Depends(get_current_user),
):
    """
    Initiate a credit purchase.

    Creates a Razorpay QR code and a pending purchase record in the DB.
    The user scans the QR to pay; the webhook handles crediting.
    """
    try:
        # Create Razorpay QR
        qr_data = await razorpay.create_qr_code(
            amount_inr=req.amount_inr, user_email=user.email
        )

        # Insert a pending purchase record
        async with db._get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO credit_purchases
                    (user_email, amount_inr, credits_added,
                     razorpay_order_id, status)
                VALUES ($1, $2, $3, $4, 'pending')
                RETURNING id
                """,
                user.email,
                req.amount_inr,
                qr_data["credits_to_add"],
                qr_data["order_id"],
            )

        return CreditPurchaseResponse(
            qr_code_url=qr_data["qr_code_url"],
            qr_code_id=qr_data["qr_code_id"],
            order_id=qr_data["order_id"],
            amount_inr=req.amount_inr,
            credits_to_add=qr_data["credits_to_add"],
            purchase_id=str(row["id"]),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[Credits] Purchase failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create payment QR")
