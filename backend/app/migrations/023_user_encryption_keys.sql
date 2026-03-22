-- Migration 023: User Encryption Keys
-- Adds a public key column to user_credits to support Zero-Knowledge Encryption.

ALTER TABLE user_credits
ADD COLUMN IF NOT EXISTS encryption_public_key TEXT;

COMMENT ON COLUMN user_credits.encryption_public_key IS 'SPKI-formatted ECC Public Key (P-256) for Zero-Knowledge encryption.';
