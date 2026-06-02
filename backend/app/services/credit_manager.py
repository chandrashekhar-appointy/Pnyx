"""
Credit Manager — Atomic credit deductions via Redis Lua script.

This is the core engine of the credit system. It uses a Redis Lua script
to atomically check and deduct credits across three pools:
  1. Weekly free credits  (highest priority, consumed first)
  2. Admin bonus credits  (mid priority)
  3. Purchased credits    (lowest priority, consumed last)

Postgres is the Source of Truth. If Redis keys are missing (e.g. after a
Redis restart), the manager re-populates them from Postgres before proceeding.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import redis.asyncio as aioredis

try:
    from ..db import DatabaseManager
    from .ledger_service import LedgerService
except (ImportError, ValueError):
    from db import DatabaseManager
    from services.ledger_service import LedgerService

logger = logging.getLogger(__name__)

# Default soft limit: allow going this far negative before blocking.
# Set very generous to implement "grace period" — never cut off mid-meeting.
# New meetings are blocked at the WebSocket connection level when balance <= 0.
DEFAULT_SOFT_LIMIT = -50000

# Weekly free credits allocation
WEEKLY_FREE_CREDITS = 10000

# ────────────────────────────────────────────────────────────────────
# Redis Lua script for atomic multi-pool deduction
# ────────────────────────────────────────────────────────────────────
DEDUCT_CREDITS_LUA = """
-- KEYS[1] = weekly, KEYS[2] = admin, KEYS[3] = purchased
-- ARGV[1] = cost, ARGV[2] = soft_limit (e.g., -100)

local cost = tonumber(ARGV[1])
local soft_limit = tonumber(ARGV[2])

local w = tonumber(redis.call('GET', KEYS[1]) or '0')
local a = tonumber(redis.call('GET', KEYS[2]) or '0')
local p = tonumber(redis.call('GET', KEYS[3]) or '0')

-- Check total against soft limit
if (w + a + p - cost) < soft_limit then
    return {0, w, a, p}  -- 0 = Blocked (Quota Exhausted)
end

local rem = cost

-- Deduct from Weekly first
if w > 0 then
    local dec = math.min(w, rem)
    w = w - dec
    rem = rem - dec
    redis.call('SET', KEYS[1], w)
end

-- Deduct from Admin Bonus
if rem > 0 and a > 0 then
    local dec = math.min(a, rem)
    a = a - dec
    rem = rem - dec
    redis.call('SET', KEYS[2], a)
end

-- Deduct from Purchased
if rem > 0 and p > 0 then
    local dec = math.min(p, rem)
    p = p - dec
    rem = rem - dec
    redis.call('SET', KEYS[3], p)
end

-- Handle Soft Limit overlap (deduct remainder from weekly to show negative)
if rem > 0 then
    w = w - rem
    redis.call('SET', KEYS[1], w)
end

return {1, w, a, p}  -- 1 = Success
"""


def _current_iso_week() -> str:
    """Return the current ISO week string, e.g. '2026-W12'."""
    now = datetime.now(timezone.utc)
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


def _redis_keys(user_email: str) -> Tuple[str, str, str, str]:
    """Return the four Redis keys for a user's credit pools + week marker."""
    prefix = f"user:{user_email}:credits"
    return (
        f"{prefix}:weekly",
        f"{prefix}:admin",
        f"{prefix}:purchased",
        f"{prefix}:reset_week",
    )


class CreditManager:
    """
    Manages credit balances, atomic deductions, and synchronisation
    between Redis (fast path) and Postgres (source of truth).
    """

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self.ledger = LedgerService(self.db)

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        
        # In development, fallback to fakeredis if real Redis is missing
        if os.getenv("ENVIRONMENT") == "development" or os.getenv("NODE_ENV") == "development":
            try:
                # Ping test
                import redis
                r = redis.from_url(redis_url)
                r.ping()
                self.redis: aioredis.Redis = aioredis.from_url(
                    redis_url, decode_responses=True
                )
            except Exception:
                logger.warning("[CreditManager] Redis not reachable, falling back to fakeredis")
                import fakeredis.aioredis
                self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        else:
            self.redis: aioredis.Redis = aioredis.from_url(
                redis_url, decode_responses=True
            )

        # Pre-register the Lua script (will be loaded on first use)
        self._deduct_script = self.redis.register_script(DEDUCT_CREDITS_LUA)

    # ── Ensure user exists in Postgres ──────────────────────────────

    async def _ensure_user_row(self, user_email: str) -> Dict:
        """
        Ensure a row exists in `user_credits` for this user.
        Returns the row as a dict.
        """
        async with self.db._get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_credits WHERE user_email = $1",
                user_email,
            )
            if row:
                return dict(row)

            # First-time user — insert defaults
            current_week = _current_iso_week()
            await conn.execute(
                """
                INSERT INTO user_credits
                    (user_email, weekly_quota, purchased_credits,
                     admin_bonus_credits, is_unlimited, last_reset_week)
                VALUES ($1, $2, 0, 0, FALSE, $3)
                ON CONFLICT (user_email) DO NOTHING
                """,
                user_email,
                WEEKLY_FREE_CREDITS,
                current_week,
            )
            row = await conn.fetchrow(
                "SELECT * FROM user_credits WHERE user_email = $1",
                user_email,
            )
            return dict(row)

    # ── Redis ↔ Postgres Sync ───────────────────────────────────────

    async def _ensure_redis_synced(self, user_email: str) -> None:
        """
        Ensure Redis credit keys are populated and that the weekly
        credit quota has been reset if a new ISO week has started.

        The reset_week key in Redis tracks which week the current
        credit values belong to.  This makes the week-boundary check
        a fast Redis-only operation on the hot path.
        """
        wk, ak, pk, rwk = _redis_keys(user_email)
        current_week = _current_iso_week()

        # Fast path: keys exist AND we're still in the same week
        cached_week = await self.redis.get(rwk)
        if cached_week == current_week and await self.redis.exists(wk):
            return

        # Either keys are missing (Redis restart / first call) or the
        # week has changed — fetch Postgres state and reconcile.
        user = await self._ensure_user_row(user_email)

        if cached_week and cached_week != current_week and await self.redis.exists(wk):
            # ── Week boundary crossed while Redis keys still exist ──
            # Reset weekly credits; leave admin & purchased untouched.
            logger.info(
                f"[CreditManager] Week boundary crossed for {user_email}: "
                f"{cached_week} → {current_week}  — resetting weekly to {WEEKLY_FREE_CREDITS}"
            )
            pipe = self.redis.pipeline()
            pipe.set(wk, WEEKLY_FREE_CREDITS)
            pipe.set(rwk, current_week)
            await pipe.execute()

            # Persist to Postgres
            async with self.db._get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE user_credits
                    SET weekly_quota = $1, last_reset_week = $2,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_email = $3
                    """,
                    WEEKLY_FREE_CREDITS,
                    current_week,
                    user_email,
                )
            return

        # ── Full resync from Postgres (keys missing) ────────────────
        weekly = user["weekly_quota"]
        if user["last_reset_week"] != current_week:
            weekly = WEEKLY_FREE_CREDITS
            # Update Postgres too while we're here
            async with self.db._get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE user_credits
                    SET weekly_quota = $1, last_reset_week = $2,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_email = $3
                    """,
                    weekly,
                    current_week,
                    user_email,
                )

        pipe = self.redis.pipeline()
        pipe.set(wk, weekly)
        pipe.set(ak, user["admin_bonus_credits"])
        pipe.set(pk, user["purchased_credits"])
        pipe.set(rwk, current_week)
        await pipe.execute()

        logger.info(
            f"[CreditManager] Synced Redis for {user_email}: "
            f"weekly={weekly}, admin={user['admin_bonus_credits']}, "
            f"purchased={user['purchased_credits']}, week={current_week}"
        )

    # ── Write-back: Redis → Postgres ────────────────────────────────

    async def _sync_to_postgres(
        self, user_email: str, weekly: int, admin: int, purchased: int
    ) -> None:
        """Write Redis balances back to Postgres (fire-and-forget)."""
        try:
            async with self.db._get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE user_credits
                    SET weekly_quota = $1,
                        admin_bonus_credits = $2,
                        purchased_credits = $3,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_email = $4
                    """,
                    weekly,
                    admin,
                    purchased,
                    user_email,
                )
        except Exception as e:
            logger.error(
                f"[CreditManager] Postgres sync failed for {user_email}: {e}",
                exc_info=True,
            )

    # ── Public API ──────────────────────────────────────────────────

    async def is_unlimited(self, user_email: str) -> bool:
        """Check if the user has unlimited credits."""
        user = await self._ensure_user_row(user_email)
        return user.get("is_unlimited", False)

    async def get_balance(self, user_email: str) -> Dict:
        """
        Return the user's current credit balance from Redis.

        Returns: {weekly, admin, purchased, total, is_unlimited}
        """
        user = await self._ensure_user_row(user_email)

        if user.get("is_unlimited"):
            return {
                "weekly": WEEKLY_FREE_CREDITS,
                "admin": 0,
                "purchased": 0,
                "total": WEEKLY_FREE_CREDITS,
                "is_unlimited": True,
            }

        await self._ensure_redis_synced(user_email)
        wk, ak, pk, _rwk = _redis_keys(user_email)

        pipe = self.redis.pipeline()
        pipe.get(wk)
        pipe.get(ak)
        pipe.get(pk)
        results = await pipe.execute()

        weekly = int(results[0] or 0)
        admin = int(results[1] or 0)
        purchased = int(results[2] or 0)

        return {
            "weekly": weekly,
            "admin": admin,
            "purchased": purchased,
            "total": weekly + admin + purchased,
            "is_unlimited": False,
        }

    async def deduct_credits(
        self,
        user_email: str,
        cost: int,
        reference_id: Optional[str] = None,
        soft_limit: int = DEFAULT_SOFT_LIMIT,
        log_ledger: bool = True,
        sync_postgres: bool = True,
    ) -> Dict:
        """
        Atomically deduct `cost` credits from the user's pools.

        Returns:
            {
                "allowed": bool,
                "weekly": int,
                "admin": int,
                "purchased": int,
                "total": int,
            }
        """
        # Unlimited users bypass everything
        if await self.is_unlimited(user_email):
            return {
                "allowed": True,
                "weekly": WEEKLY_FREE_CREDITS,
                "admin": 0,
                "purchased": 0,
                "total": WEEKLY_FREE_CREDITS,
            }

        await self._ensure_redis_synced(user_email)
        wk, ak, pk, _rwk = _redis_keys(user_email)

        # Execute the Lua script atomically
        try:
            result = await self._deduct_script(
                keys=[wk, ak, pk],
                args=[cost, soft_limit],
            )
        except Exception as e:
            logger.warning(f"[CreditManager] Lua script deduction failed (likely fakeredis without lupa): {e}. Falling back to Python-based deduction.")
            w_val = await self.redis.get(wk)
            a_val = await self.redis.get(ak)
            p_val = await self.redis.get(pk)
            
            w = int(w_val) if w_val else 0
            a = int(a_val) if a_val else 0
            p = int(p_val) if p_val else 0
            
            if (w + a + p - cost) < soft_limit:
                result = [0, w, a, p]
            else:
                rem = cost
                if w > 0:
                    dec = min(w, rem)
                    w -= dec
                    rem -= dec
                    await self.redis.set(wk, w)
                
                if rem > 0 and a > 0:
                    dec = min(a, rem)
                    a -= dec
                    rem -= dec
                    await self.redis.set(ak, a)
                    
                if rem > 0 and p > 0:
                    dec = min(p, rem)
                    p -= dec
                    rem -= dec
                    await self.redis.set(pk, p)
                    
                if rem > 0:
                    w -= rem
                    await self.redis.set(wk, w)
                    
                result = [1, w, a, p]

        allowed = bool(result[0])
        weekly = int(result[1])
        admin = int(result[2])
        purchased = int(result[3])
        total = weekly + admin + purchased

        if allowed:
            if log_ledger:
                # Log to ledger (best-effort, don't block the caller)
                await self.ledger.log_transaction(
                    user_email=user_email,
                    change=-cost,
                    source="usage",
                    reference_id=reference_id,
                    balance_after=total,
                )

            if sync_postgres:
                # Async write-back to Postgres
                await self._sync_to_postgres(user_email, weekly, admin, purchased)

        logger.debug(
            f"[CreditManager] deduct {cost} for {user_email}: "
            f"allowed={allowed}, remaining={total}"
        )

        return {
            "allowed": allowed,
            "weekly": weekly,
            "admin": admin,
            "purchased": purchased,
            "total": total,
        }

    async def flush_usage_batch(
        self,
        user_email: str,
        meeting_id: str,
        usage_credits: int,
        weekly: int,
        admin: int,
        purchased: int,
        finalize: bool = False,
    ) -> None:
        """
        Flush an aggregated streaming usage batch to Postgres.
        """
        if usage_credits <= 0:
            return

        total = int(weekly) + int(admin) + int(purchased)
        await self.db.upsert_meeting_credit_usage(
            meeting_id=meeting_id,
            user_email=user_email,
            credits_used_delta=int(usage_credits),
            balance_after=total,
            finalize=finalize,
        )
        await self._sync_to_postgres(user_email, int(weekly), int(admin), int(purchased))

    async def add_purchased_credits(
        self,
        user_email: str,
        amount: int,
        purchase_id: Optional[str] = None,
    ) -> Dict:
        """Add purchased credits (from Razorpay payment)."""
        await self._ensure_user_row(user_email)
        await self._ensure_redis_synced(user_email)

        _, _, pk, _ = _redis_keys(user_email)

        # Atomically increment in Redis
        await self.redis.incrby(pk, amount)

        # Update Postgres
        async with self.db._get_connection() as conn:
            await conn.execute(
                """
                UPDATE user_credits
                SET purchased_credits = purchased_credits + $1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_email = $2
                """,
                amount,
                user_email,
            )

        balance = await self.get_balance(user_email)

        # Ledger
        await self.ledger.log_transaction(
            user_email=user_email,
            change=amount,
            source="purchase",
            reference_id=purchase_id,
            pool="purchased",
            balance_after=balance["total"],
        )

        logger.info(
            f"[CreditManager] Added {amount} purchased credits for {user_email}"
        )
        return balance

    async def add_admin_credits(
        self,
        user_email: str,
        amount: int,
        reason: str,
        admin_email: str,
    ) -> Dict:
        """
        Add (or remove) admin bonus credits with audit trail.

        Args:
            amount: Positive to add, negative to subtract.
            reason: Human-readable reason for the override.
            admin_email: Email of the admin performing the action.
        """
        await self._ensure_user_row(user_email)
        await self._ensure_redis_synced(user_email)

        _, ak, _, _ = _redis_keys(user_email)

        # Update Redis
        await self.redis.incrby(ak, amount)

        # Update Postgres user_credits
        async with self.db._get_connection() as conn:
            await conn.execute(
                """
                UPDATE user_credits
                SET admin_bonus_credits = admin_bonus_credits + $1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_email = $2
                """,
                amount,
                user_email,
            )

            # Insert override record
            override_row = await conn.fetchrow(
                """
                INSERT INTO credit_overrides
                    (user_email, credits_added, reason, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                user_email,
                amount,
                reason,
                admin_email,
            )

        balance = await self.get_balance(user_email)

        # Ledger
        await self.ledger.log_transaction(
            user_email=user_email,
            change=amount,
            source="admin",
            reference_id=str(override_row["id"]) if override_row else None,
            pool="admin",
            balance_after=balance["total"],
        )

        logger.info(
            f"[CreditManager] Admin {admin_email} adjusted {amount:+d} credits "
            f"for {user_email}: {reason}"
        )
        return balance

    async def set_unlimited(
        self, user_email: str, is_unlimited: bool
    ) -> None:
        """Toggle the is_unlimited flag for a user."""
        await self._ensure_user_row(user_email)
        async with self.db._get_connection() as conn:
            await conn.execute(
                """
                UPDATE user_credits
                SET is_unlimited = $1, updated_at = CURRENT_TIMESTAMP
                WHERE user_email = $2
                """,
                is_unlimited,
                user_email,
            )
        logger.info(
            f"[CreditManager] Set is_unlimited={is_unlimited} for {user_email}"
        )

    async def reset_weekly_credits(self, user_email: str) -> None:
        """
        Reset weekly credits to the default allocation.
        Called by the weekly cron job and lazily on week-boundary crossing.
        """
        current_week = _current_iso_week()
        wk, _, _, rwk = _redis_keys(user_email)

        # Update Redis (credits + week marker)
        pipe = self.redis.pipeline()
        pipe.set(wk, WEEKLY_FREE_CREDITS)
        pipe.set(rwk, current_week)
        await pipe.execute()

        # Update Postgres
        async with self.db._get_connection() as conn:
            await conn.execute(
                """
                UPDATE user_credits
                SET weekly_quota = $1, last_reset_week = $2,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_email = $3
                """,
                WEEKLY_FREE_CREDITS,
                current_week,
                user_email,
            )

        # Ledger
        balance = await self.get_balance(user_email)
        await self.ledger.log_transaction(
            user_email=user_email,
            change=WEEKLY_FREE_CREDITS,
            source="reset",
            reference_id=current_week,
            pool="weekly",
            balance_after=balance["total"],
        )
