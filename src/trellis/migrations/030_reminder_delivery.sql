-- 'sent' was written BEFORE delivery: a Telegram timeout left a reminder the
-- person never received marked as sent. A reminder now moves through states
-- named for what each one shows:
--   scheduled -> claimed   (picked up; can't fire twice)
--             -> executed  (its message is ready — for a check-in, the turn has
--                           run ONCE and its reply is stored; never run again)
--             -> accepted  (Telegram took the message — not that it was read)
--             -> undelivered (retries ran out)
-- Old rows keep 'sent', which now reads honestly: marked sent, delivery never confirmed.
ALTER TABLE reminders DROP CONSTRAINT IF EXISTS reminders_status_check;
ALTER TABLE reminders ADD CONSTRAINT reminders_status_check
    CHECK (status IN ('scheduled', 'claimed', 'executed', 'accepted', 'undelivered', 'cancelled', 'sent'));
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS message TEXT;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS reminders_awaiting_delivery ON reminders (user_id) WHERE status IN ('claimed', 'executed');
