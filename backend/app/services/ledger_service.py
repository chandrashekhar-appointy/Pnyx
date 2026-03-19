"""
Ledger Service — Immutable audit log for all credit movements.

Every credit change (usage, purchase, admin override, reset, refund) is
recorded here for post-hoc reconciliation and analytics.
"""

import logging
from typing import Optional

try:
    from ..db import DatabaseManager
except (ImportError, ValueError):
    from db import DatabaseManager

logger = logging.getLogger(__name__)


class LedgerService:
    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()

    async def log_transaction(
        self,
        user_email: str,
        change: int,
        source: str,
        reference_id: Optional[str] = None,
        pool: Optional[str] = None,
        balance_after: Optional[int] = None,
    ) -> None:
        """
        Write an immutable ledger entry.

        Args:
            user_email: The user this transaction belongs to.
            change:     Positive (credit added) or negative (credit used).
            source:     One of 'usage', 'purchase', 'admin', 'refund', 'reset'.
            reference_id: Meeting ID, purchase ID, override ID, etc.
            pool:       Which pool was affected: 'weekly', 'admin', 'purchased'.
            balance_after: Snapshot of user's total balance after this txn.
        """
        try:
            async with self.db._get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO credit_ledger
                        (user_email, change, source, reference_id, pool, balance_after)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    user_email,
                    change,
                    source,
                    reference_id,
                    pool,
                    balance_after,
                )
            logger.info(
                f"[Ledger] {user_email}: {change:+d} credits "
                f"(source={source}, ref={reference_id}, pool={pool})"
            )
        except Exception as e:
            # Ledger writes should never block the main flow — log and continue
            logger.error(f"[Ledger] Failed to log transaction: {e}", exc_info=True)

    async def get_history(
        self,
        user_email: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """Return paginated ledger entries for a user, newest first."""
        async with self.db._get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, change, source, reference_id, pool, balance_after, created_at
                FROM credit_ledger
                WHERE user_email = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_email,
                limit,
                offset,
            )
            return [
                {
                    "id": str(row["id"]),
                    "change": row["change"],
                    "source": row["source"],
                    "reference_id": row["reference_id"],
                    "pool": row["pool"],
                    "balance_after": row["balance_after"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                }
                for row in rows
            ]
