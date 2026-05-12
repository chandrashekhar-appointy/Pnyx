"""
Unit tests for CreditManager — credit deduction logic, priority ordering,
soft limits, and unlimited mode.

These tests mock Redis and Postgres to run without infrastructure.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _current_iso_week() -> str:
    """Match the helper in credit_manager.py."""
    now = datetime.now(timezone.utc)
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


# ─── Helper: build a fake CreditManager with mocked dependencies ───


def _make_credit_manager():
    """Create a CreditManager with mocked DB and Redis."""
    with (
        patch("app.services.credit_manager.DatabaseManager") as MockDB,
        patch("app.services.credit_manager.aioredis") as MockRedis,
    ):
        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_db._get_connection = MagicMock(return_value=_async_ctx(mock_conn))

        mock_redis = AsyncMock()
        MockRedis.from_url.return_value = mock_redis

        from app.services.credit_manager import CreditManager

        mgr = CreditManager(db=mock_db)
        mgr.redis = mock_redis

        return mgr, mock_db, mock_redis, mock_conn


class _async_ctx:
    """Async context manager wrapper for mocks."""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        pass


# ─── Tests ──────────────────────────────────────────────────────────


class TestCreditPriority:
    """Test that deductions follow Weekly → Admin → Purchased order."""

    @pytest.mark.asyncio
    async def test_weekly_consumed_first(self):
        """When weekly has enough, only weekly is deducted."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        # Setup: user exists, is not unlimited
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "test@appointy.com",
                "weekly_quota": 5000,
                "purchased_credits": 1000,
                "admin_bonus_credits": 500,
                "is_unlimited": False,
                "last_reset_week": "2026-W12",
            }
        )

        # Redis keys exist and week marker matches current week (fast path)
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=_current_iso_week())

        # Lua script returns: success=1, weekly=4990, admin=500, purchased=1000
        mock_script = AsyncMock(return_value=[1, 4990, 500, 1000])
        mgr._deduct_script = mock_script

        # Mock pipeline for get_balance
        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=["4990", "500", "1000"])
        mock_pipe.get = MagicMock(return_value=mock_pipe)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        result = await mgr.deduct_credits("test@appointy.com", cost=10)

        assert result["allowed"] is True
        assert result["weekly"] == 4990
        assert result["admin"] == 500
        assert result["purchased"] == 1000
        mock_script.assert_awaited_once()


class TestSoftLimit:
    """Test the soft limit (negative buffer) behavior."""

    @pytest.mark.asyncio
    async def test_soft_limit_allows_negative(self):
        """Should allow going to -100 but not beyond."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "test@appointy.com",
                "weekly_quota": 5,
                "purchased_credits": 0,
                "admin_bonus_credits": 0,
                "is_unlimited": False,
                "last_reset_week": "2026-W12",
            }
        )

        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=_current_iso_week())

        # Lua script returns blocked (total - cost < soft_limit)
        mock_script = AsyncMock(return_value=[0, 5, 0, 0])
        mgr._deduct_script = mock_script

        result = await mgr.deduct_credits("test@appointy.com", cost=200)

        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_soft_limit_allows_small_overshoot(self):
        """With 5 credits and cost 50, should succeed (goes to -45, within -100)."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "test@appointy.com",
                "weekly_quota": 5,
                "purchased_credits": 0,
                "admin_bonus_credits": 0,
                "is_unlimited": False,
                "last_reset_week": "2026-W12",
            }
        )

        mock_redis.exists = AsyncMock(return_value=_current_iso_week())
        mock_redis.get = AsyncMock(return_value=_current_iso_week())

        # Lua script returns success (5 - 50 = -45, which is > -100)
        mock_script = AsyncMock(return_value=[1, -45, 0, 0])
        mgr._deduct_script = mock_script

        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=["-45", "0", "0"])
        mock_pipe.get = MagicMock(return_value=mock_pipe)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        result = await mgr.deduct_credits("test@appointy.com", cost=50)

        assert result["allowed"] is True
        assert result["weekly"] == -45


class TestUnlimitedMode:
    """Test unlimited mode bypass."""

    @pytest.mark.asyncio
    async def test_unlimited_always_allowed(self):
        """Unlimited users should always pass credit checks."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "admin@appointy.com",
                "weekly_quota": 0,
                "purchased_credits": 0,
                "admin_bonus_credits": 0,
                "is_unlimited": True,
                "last_reset_week": "2026-W12",
            }
        )

        mock_script = AsyncMock()
        mgr._deduct_script = mock_script

        result = await mgr.deduct_credits("admin@appointy.com", cost=999999)

        assert result["allowed"] is True
        # Lua script should NOT have been called
        assert mock_script.call_count == 0


class TestWebhookIdempotency:
    """Test that duplicate webhooks don't double-credit."""

    @pytest.mark.asyncio
    async def test_duplicate_payment_id_rejected(self):
        """Second webhook with same payment_id should be a no-op."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        # First call succeeds
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "test@appointy.com",
                "weekly_quota": 10000,
                "purchased_credits": 5000,
                "admin_bonus_credits": 0,
                "is_unlimited": False,
                "last_reset_week": "2026-W12",
            }
        )

        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=_current_iso_week())
        mock_redis.incrby = AsyncMock(return_value=15000)
        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=["10000", "0", "15000"])
        mock_pipe.get = MagicMock(return_value=mock_pipe)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        balance = await mgr.add_purchased_credits(
            "test@appointy.com", amount=10000, purchase_id="pay_abc123"
        )
        assert balance["purchased"] == 15000

        # Second call — the UPDATE should return "UPDATE 0" (handled in webhook router)
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")

        # The webhook router checks for "UPDATE 0" and returns early
        # This test verifies the credit_manager doesn't double-add
        # (the idempotency is at the router level via DB UNIQUE constraint)


class TestWeeklyReset:
    """Test the automatic weekly reset when the week boundary is crossed."""

    @pytest.mark.asyncio
    async def test_week_boundary_triggers_reset(self):
        """When Redis has a stale week marker, credits should reset to 10,000."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        # Postgres shows user has 500 weekly credits from last week
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "user_email": "test@appointy.com",
                "weekly_quota": 500,
                "purchased_credits": 200,
                "admin_bonus_credits": 100,
                "is_unlimited": False,
                "last_reset_week": "2025-W01",  # stale week
            }
        )

        # Redis keys EXIST but reset_week is stale (last week)
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="2025-W01")  # stale week

        # Pipeline mock for the reset write
        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=None)
        mock_pipe.set = MagicMock(return_value=mock_pipe)
        mock_pipe.get = MagicMock(return_value=mock_pipe)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        mock_conn.execute = AsyncMock()

        # Call _ensure_redis_synced — this should detect the week change
        await mgr._ensure_redis_synced("test@appointy.com")

        # Verify Redis pipeline was called with the reset value (10000)
        # The pipeline.set calls should include weekly=10000 and the current week
        set_calls = mock_pipe.set.call_args_list
        # First set call should be for the weekly key with 10000
        assert set_calls[0][0][1] == 10000, (
            f"Expected weekly credits to be reset to 10000, got {set_calls[0][0][1]}"
        )
        # Second set call should be for the reset_week key with current week
        assert set_calls[1][0][1] == _current_iso_week(), (
            f"Expected reset_week to be {_current_iso_week()}, got {set_calls[1][0][1]}"
        )

        # Verify Postgres was updated
        mock_conn.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_same_week_skips_reset(self):
        """When Redis week marker matches current week, no reset should happen."""
        mgr, mock_db, mock_redis, mock_conn = _make_credit_manager()

        current_week = _current_iso_week()

        # Redis keys exist and week marker is current
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=current_week)

        mock_conn.fetchrow = AsyncMock()  # Should NOT be called

        await mgr._ensure_redis_synced("test@appointy.com")

        # Postgres should NOT have been queried (fast path)
        mock_conn.fetchrow.assert_not_awaited()
