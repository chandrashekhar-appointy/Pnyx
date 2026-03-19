-- Migration 017: Credit System - Users, Purchases, Overrides, Ledger
-- Implements the credit economy for ElevenLabs STT quota limiting.

-- ============================================================
-- 1. User Credits Table (keyed by user_email to match codebase)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_credits (
    user_email      VARCHAR(255) PRIMARY KEY,
    weekly_quota    INTEGER NOT NULL DEFAULT 10000,
    purchased_credits INTEGER NOT NULL DEFAULT 0,
    admin_bonus_credits INTEGER NOT NULL DEFAULT 0,
    is_unlimited    BOOLEAN NOT NULL DEFAULT FALSE,
    last_reset_week VARCHAR(10) DEFAULT NULL,  -- e.g. '2026-W12'
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. Credit Purchases (Razorpay payments)
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_purchases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email          VARCHAR(255) NOT NULL,
    amount_inr          INTEGER NOT NULL,
    credits_added       INTEGER NOT NULL,
    razorpay_payment_id VARCHAR(255) UNIQUE,  -- idempotency key
    razorpay_order_id   VARCHAR(255),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | success | failed
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_credit_purchases_user
    ON credit_purchases (user_email);

CREATE INDEX IF NOT EXISTS idx_credit_purchases_status
    ON credit_purchases (status);

-- ============================================================
-- 3. Credit Overrides (admin manual adjustments)
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email      VARCHAR(255) NOT NULL,
    credits_added   INTEGER NOT NULL,  -- positive = add, negative = remove
    reason          TEXT NOT NULL,
    created_by      VARCHAR(255) NOT NULL,  -- admin email
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_credit_overrides_user
    ON credit_overrides (user_email);

-- ============================================================
-- 4. Credit Ledger (immutable audit log of ALL credit movements)
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email      VARCHAR(255) NOT NULL,
    change          INTEGER NOT NULL,        -- e.g. -15 (usage), +10000 (purchase)
    source          VARCHAR(20) NOT NULL,    -- usage | purchase | admin | refund | reset
    reference_id    VARCHAR(255),            -- meeting_id, purchase_id, override_id
    pool            VARCHAR(20),             -- weekly | admin | purchased (which pool was affected)
    balance_after   INTEGER,                 -- snapshot of total balance after this txn
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_user
    ON credit_ledger (user_email);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_source
    ON credit_ledger (source);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_created
    ON credit_ledger (created_at DESC);
