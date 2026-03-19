# App Credit System & Razorpay QR Payments (V2 - Flexible & Robust)

## Overview
This document outlines the implementation plan for an **atomic, flexible, and robust** credit-based quota limiting system for the ElevenLabs Speech-to-Text (STT) API in Meeting Co-Pilot.

This upgraded architecture ensures zero race conditions, prioritizes different pools of credits, allows admin overrides, logs every transaction into an immutable ledger, and handles real-time UI updates seamlessly without polling.

---

## 1. Credit Economy Rules & Priority Logic
**Cost Rate:** ~0.3 credits per second of STT processing.

**Credit Priorities (Usage Order):**
1. **Weekly Free Credits** (10,000 per week, resets Monday 00:00 UTC)
2. **Admin Bonus Credits** (Granted manually for support/promotions)
3. **Purchased Credits** (Bought via Razorpay, never expire)

*Rule: Purchased credits are NEVER consumed before Weekly and Admin credits are fully exhausted.*

**Unlimited Mode:** Users flagged as `is_unlimited = True` bypass all credit checks completely (used for internal testing, demos, core team).

**Soft Limit:** To prevent cutting users off mid-sentence, the system allows a negative buffer (e.g., `-100` credits) on the final deduction before strictly blocking STT.

---

## 2. Database Schema Expansion (PostgreSQL)

### Updated `users` table
| Column | Type | Description |
|--------|------|-------------|
| `weekly_quota` | INTEGER | Default 10000 |
| `purchased_credits` | INTEGER | Default 0 |
| `admin_bonus_credits` | INTEGER | Default 0 (NEW) |
| `is_unlimited` | BOOLEAN | Default FALSE (NEW) |
| `last_reset_week` | VARCHAR | e.g., '2026-W05' |

### Updated `credit_purchases` table
| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | PK |
| `user_id` | UUID | FK -> users.id |
| `amount_inr` | INTEGER | |
| `credits_added` | INTEGER | |
| `razorpay_payment_id` | VARCHAR | **UNIQUE (Ensures Webhook Idempotency)** |
| `razorpay_order_id` | VARCHAR | |
| `status` | VARCHAR | 'pending', 'success', 'failed' |
| `created_at` | TIMESTAMP | |

### NEW: `credit_overrides` table
Logs manual adjustments made by administrators.
| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | PK |
| `user_id` | UUID | FK -> users.id |
| `credits_added` | INTEGER | Can be positive or negative |
| `reason` | TEXT | E.g., "Refund for failed meeting" |
| `created_by` | UUID | Admin User ID |
| `created_at` | TIMESTAMP | |

### NEW: `credit_ledger` table
An immutable audit log of ALL credit movements.
| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | PK |
| `user_id` | UUID | FK -> users.id |
| `change` | INTEGER | E.g., -15 (usage), +10000 (purchase) |
| `source` | VARCHAR | 'usage', 'purchase', 'admin', 'refund', 'reset' |
| `reference_id` | VARCHAR | Meeting ID, Purchase ID, Override ID |
| `created_at` | TIMESTAMP | |

---

## 3. Redis Key Design & Atomic Deductions

To eliminate race conditions during concurrent WebSocket chunks, we use an atomic **Redis Lua Script** for deductions.

### Redis Keys
- `user:{user_id}:credits:weekly` (Integer, TTL set to end of current week)
- `user:{user_id}:credits:admin` (Integer)
- `user:{user_id}:credits:purchased` (Integer)

### Atomic Lua Script (`deduct_credits.lua`)
```lua
-- KEYS[1] = weekly, KEYS[2] = admin, KEYS[3] = purchased
-- ARGV[1] = cost, ARGV[2] = soft_limit (e.g., -100)

local cost = tonumber(ARGV[1])
local soft_limit = tonumber(ARGV[2])

local w = tonumber(redis.call('GET', KEYS[1]) or '0')
local a = tonumber(redis.call('GET', KEYS[2]) or '0')
local p = tonumber(redis.call('GET', KEYS[3]) or '0')

-- Check total against soft limit
if (w + a + p - cost) < soft_limit then
    return {0, w, a, p} -- 0 = Blocked (Quota Exhausted)
end

local rem = cost

-- Deduct from Weekly
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

-- Handle Soft Limit overlap (deduct from weekly to show negative buffer)
if rem > 0 then
    w = w - rem
    redis.call('SET', KEYS[1], w)
end

return {1, w, a, p} -- 1 = Success
```

---

## 4. Cost Control Guardrails

To prevent excessive credit burn and save money on ElevenLabs:
1. **Silence Detection**: The existing `vad.py` (Voice Activity Detection) ensures that silent chunks are **dropped completely** and not sent to STT. No credits are charged for silence.
2. **Idle Timeout**: If no speech is detected for `15 minutes`, automatically pause transcription and require the user to click "Resume".
3. **Max Session Duration**: Hard cap meetings at `4 hours` per continuous session.

---

## 5. Webhook Safety & Idempotency
`POST /api/payments/webhook`
1. Validates `x-razorpay-signature`.
2. Attempts to insert into `credit_purchases` (or update status to 'success'). 
3. Rely on `UNIQUE(razorpay_payment_id)`: If Razorpay fires the webhook twice, the DB rejects the duplicate, preventing double-crediting.
4. Updates Postgres `purchased_credits` AND Redis `user:{user_id}:credits:purchased`.
5. Logs to `credit_ledger`.
6. **Push UI Update**: Emits a WebSocket broadcast to the user's active meeting session: `{"type": "credit_update", "status": "payment_successful", "credits": 10000}`.

---

## 6. Frontend Improvements (Next.js)

**Goodbye Polling, Hello Push:**
Instead of polling `/credits` every 3 seconds while the QR modal is open, the frontend listens to the active WebSocket connection.
When the Razorpay webhook succeeds, the backend pushes a `payment_successful` event. The QR modal auto-closes, updates the UI credit bar, and immediately resumes audio streaming.

---

## 7. Weekly Reset Mechanism
**Cron Job (Server-side):** Runs at `Monday 00:00 UTC`.
- Sets Postgres `last_reset_week` to the new week.
- Uses Redis `PIPELINE` to reset `user:{user_id}:credits:weekly` to `10000` for all active users.
- Writes a "reset" entry to `credit_ledger` to maintain audit accuracy.

---

## 8. Monitoring & Metrics
A dedicated analytics dashboard (internal) to track:
1. **Credit Burn Rate**: Total STT calls per day vs actual ElevenLabs invoice.
2. **Audio Efficiency**: Track `credits_used` vs `actual_audio_seconds` to measure how well the VAD (Voice Activity Detection) is discarding silence.

---

## 9. Suggested Service Structure (Backend)

```text
backend/app/
├── services/
│   ├── credit_manager.py     # Contains Lua script, atomic deductions, `is_unlimited` check
│   ├── ledger_service.py     # Writes immutable logs to `credit_ledger`
│   ├── admin_service.py      # Handles manual `credit_overrides`
│   └── razorpay_client.py    # Generates QR, validates webhooks
├── api/
│   ├── credits.py            # /credits endpoints
│   ├── admin.py              # Endpoints for team to add/remove credits
│   └── qr_payments.py        # /qr and webhook endpoints
├── tasks/
│   └── weekly_reset.py       # UTC Cron job logic
└── models/
    └── credits.py            # SQLAlchemy models for overrides, ledger, purchases
```