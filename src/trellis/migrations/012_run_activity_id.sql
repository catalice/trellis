-- Runs get their Garmin identity: dedupe on activity_id (the real identity),
-- not the (date, ~distance) heuristic that collapsed two same-distance runs
-- on one day. Pre-migration rows keep NULL and fall back to the old heuristic.
ALTER TABLE training_runs ADD COLUMN IF NOT EXISTS garmin_activity_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS training_runs_user_garmin_activity
    ON training_runs (user_id, garmin_activity_id)
    WHERE garmin_activity_id IS NOT NULL;
