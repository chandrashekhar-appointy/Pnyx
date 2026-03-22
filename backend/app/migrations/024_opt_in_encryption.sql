-- Add encryption_enabled toggle to user_credits
-- Default is FALSE (Opt-in)

ALTER TABLE user_credits 
ADD COLUMN IF NOT EXISTS encryption_enabled BOOLEAN DEFAULT FALSE;
