-- Standalone reminders: make task_id optional, add label for non-task reminders
ALTER TABLE reminders ALTER COLUMN task_id DROP NOT NULL;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS label TEXT;

-- Insights: add fields for snooze/resolve/reject responses
ALTER TABLE insights ADD COLUMN IF NOT EXISTS dismissed_reason TEXT;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS snooze_until DATE;
