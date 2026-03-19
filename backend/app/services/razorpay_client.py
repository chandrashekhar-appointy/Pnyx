"""
Razorpay Client — QR Code generation and webhook signature verification.

Requires environment variables:
  - RAZORPAY_KEY_ID
  - RAZORPAY_KEY_SECRET
  - RAZORPAY_WEBHOOK_SECRET
"""

import hashlib
import hmac
import logging
import os
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Credit denomination map: INR amount -> credits
CREDIT_PACKS = {
    99: 5000,
    199: 12000,
    499: 35000,
    999: 80000,
}


class RazorpayClient:
    def __init__(self):
        self.key_id = os.getenv("RAZORPAY_KEY_ID", "")
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        self.base_url = "https://api.razorpay.com/v1"

    async def create_qr_code(
        self, amount_inr: int, user_email: str
    ) -> Optional[Dict]:
        """
        Create a Razorpay QR code for the given INR amount.

        Returns dict with: qr_code_url, order_id, amount, credits_to_add
        """
        if amount_inr not in CREDIT_PACKS:
            raise ValueError(
                f"Invalid amount. Choose from: {list(CREDIT_PACKS.keys())}"
            )

        credits_to_add = CREDIT_PACKS[amount_inr]

        # Step 1: Create an order
        order_payload = {
            "amount": amount_inr * 100,  # Razorpay expects paise
            "currency": "INR",
            "receipt": f"credit_{user_email}_{amount_inr}",
            "notes": {
                "user_email": user_email,
                "credits": str(credits_to_add),
                "purpose": "stt_credits",
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                # Create order
                order_resp = await client.post(
                    f"{self.base_url}/orders",
                    json=order_payload,
                    auth=(self.key_id, self.key_secret),
                    timeout=15.0,
                )
                order_resp.raise_for_status()
                order_data = order_resp.json()

                order_id = order_data["id"]

                # Create QR code for the order
                qr_payload = {
                    "type": "upi_qr",
                    "name": f"Pnyx Credits - {credits_to_add}",
                    "usage": "single_use",
                    "fixed_amount": True,
                    "payment_amount": amount_inr * 100,
                    "description": f"{credits_to_add} STT credits for {user_email}",
                    "notes": {
                        "order_id": order_id,
                        "user_email": user_email,
                        "credits": str(credits_to_add),
                    },
                }

                qr_resp = await client.post(
                    f"{self.base_url}/payments/qr_codes",
                    json=qr_payload,
                    auth=(self.key_id, self.key_secret),
                    timeout=15.0,
                )
                qr_resp.raise_for_status()
                qr_data = qr_resp.json()

                return {
                    "qr_code_url": qr_data.get("image_url", ""),
                    "qr_code_id": qr_data.get("id", ""),
                    "order_id": order_id,
                    "amount_inr": amount_inr,
                    "credits_to_add": credits_to_add,
                }

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[Razorpay] HTTP error creating QR: {e.response.status_code} "
                f"{e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"[Razorpay] Error creating QR code: {e}", exc_info=True)
            raise

    def verify_webhook_signature(self, payload_body: bytes, signature: str) -> bool:
        """
        Verify the Razorpay webhook signature using HMAC-SHA256.

        Args:
            payload_body: Raw request body bytes.
            signature:    Value of the `x-razorpay-signature` header.

        Returns:
            True if the signature is valid.
        """
        if not self.webhook_secret:
            logger.warning("[Razorpay] RAZORPAY_WEBHOOK_SECRET not set — rejecting webhook")
            return False

        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)
