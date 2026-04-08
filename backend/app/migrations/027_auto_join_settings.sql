-- Add auto-join setting to calendar automation
ALTER TABLE calendar_automation_settings
ADD COLUMN IF NOT EXISTS auto_join_enabled BOOLEAN NOT NULL DEFAULT FALSE;
