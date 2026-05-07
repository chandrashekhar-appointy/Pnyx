"""
Pydantic schemas for the credit system API.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


# ── Response Models ─────────────────────────────────────────────────


class CreditBalanceResponse(BaseModel):
    weekly: int
    admin: int
    purchased: int
    total: int
    is_unlimited: bool


class CreditLedgerEntry(BaseModel):
    id: str
    change: int
    source: str  # usage | purchase | admin | refund | reset
    reference_id: Optional[str] = None
    pool: Optional[str] = None
    balance_after: Optional[int] = None
    created_at: Optional[str] = None


class CreditHistoryResponse(BaseModel):
    entries: List[CreditLedgerEntry]
    total_count: Optional[int] = None


# ── Request Models ──────────────────────────────────────────────────


class CreditPurchaseRequest(BaseModel):
    amount_inr: int = Field(
        ...,
        description="INR amount for the credit pack (e.g. 99, 199, 499, 999)",
    )


class CreditPurchaseResponse(BaseModel):
    qr_code_url: str
    qr_code_id: str
    order_id: str
    amount_inr: int
    credits_to_add: int
    purchase_id: str  # DB row ID for tracking


class AdminCreditOverrideRequest(BaseModel):
    user_email: str
    credits: int = Field(
        ..., description="Positive to add, negative to subtract"
    )
    reason: str = Field(
        ..., min_length=3, description="Reason for the override"
    )


class AdminSetUnlimitedRequest(BaseModel):
    user_email: str
    is_unlimited: bool
