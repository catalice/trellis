-- Reminders grow real recurrence (audit item 24, found by live use: the Sunday
-- weekly review couldn't be set — recur_daily was the only interval).
-- daily/weekly/monthly/yearly; NULL = one-off. Existing daily reminders convert.
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS recurrence TEXT
    CHECK (recurrence IN ('daily', 'weekly', 'monthly', 'yearly'));

UPDATE reminders SET recurrence = 'daily' WHERE recur_daily = true;

ALTER TABLE reminders DROP COLUMN IF EXISTS recur_daily;
