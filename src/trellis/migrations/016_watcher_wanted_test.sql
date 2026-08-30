-- The Watcher may describe the check it WISHES it could run (wanted_test)
-- when no existing verb fits — its own requests are the verb backlog.
ALTER TABLE watcher_patterns ADD COLUMN IF NOT EXISTS wanted_test TEXT;
