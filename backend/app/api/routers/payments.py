"""
Payment Webhook Routes — Handles Razorpay payment callbacks.
"""

import json
import logging
from fastapi import APIRouter, Request, HTTPException

try:
    from ...services.credit_manager import CreditManager
    from ...services.razorpay_client import RazorpayClient
    from ...db import DatabaseManager
except (ImportError, ValueError):
    from services.credit_manager import CreditManager
    from services.razorpay_client import RazorpayClient
    from db import DatabaseManager

router = APIRouter(prefix="/api/payments", tags=["Payments"])
logger = logging.getLogger(__name__)

db = DatabaseManager()
credit_mgr = CreditManager(db)
razorpay = RazorpayClient()


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Razorpay webhook endpoint.

    Flow:
    1. Validate x-razorpay-signature
    2. Parse the payment event
    3. Update credit_purchases status (UNIQUE constraint = idempotent)
    4. Add purchased credits to user
    5. Log to ledger
    6. Broadcast credit_update via WebSocket
    """
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    # 1. Signature validation
    if not razorpay.verify_webhook_signature(body, signature):
        logger.warning("[Webhook] Invalid Razorpay signature — rejecting")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 2. Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = payload.get("event", "")
    logger.info(f"[Webhook] Received event: {event}")

    # We only care about successful payments
    if event != "payment.captured":
        return {"status": "ignored", "event": event}

    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    payment_id = payment_entity.get("id")
    order_id = payment_entity.get("order_id")
    notes = payment_entity.get("notes", {})
    user_email = notes.get("user_email")
    credits_to_add = int(notes.get("credits", 0))

    if not all([payment_id, user_email, credits_to_add]):
        logger.error(
            f"[Webhook] Missing fields: payment_id={payment_id}, "
            f"user_email={user_email}, credits={credits_to_add}"
        )
        raise HTTPException(status_code=400, detail="Missing required payment fields")

    # 3. Update credit_purchases — idempotent via UNIQUE(razorpay_payment_id)
    try:
        async with db._get_connection() as conn:
            result = await conn.execute(
                """
                UPDATE credit_purchases
                SET status = 'success',
                    razorpay_payment_id = $1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE razorpay_order_id = $2
                  AND user_email = $3
                  AND status = 'pending'
                """,
                payment_id,
                order_id,
                user_email,
            )

            if result == "UPDATE 0":
                # Either already processed (idempotent) or not found
                logger.info(
                    f"[Webhook] No pending purchase for order={order_id} — "
                    f"likely duplicate webhook, ignoring."
                )
                return {"status": "duplicate_or_not_found"}

    except Exception as e:
        # UNIQUE violation means it's a duplicate — that's fine
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            logger.info(f"[Webhook] Duplicate payment_id {payment_id} — ignoring")
            return {"status": "duplicate"}
        raise

    # 4. Add credits to user
    balance = await credit_mgr.add_purchased_credits(
        user_email=user_email,
        amount=credits_to_add,
        purchase_id=payment_id,
    )

    logger.info(
        f"[Webhook] ✅ Credited {credits_to_add} to {user_email} "
        f"(payment={payment_id}, new_total={balance['total']})"
    )

    # 5. Broadcast WebSocket event
    # The audio WebSocket manager can pick this up if the user has an active session
    # This is handled by the audio router's connection manager
    # For now we log it; the frontend will poll or listen for updates
    # TODO: integrate with active WebSocket connection manager

    return {
        "status": "success",
        "credits_added": credits_to_add,
        "new_balance": balance["total"],
    }
