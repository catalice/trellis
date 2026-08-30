-- State logs: when the state was FELT can differ from when it was SAID
-- ("this morning I was shit", said at noon). felt_at drives the timeline;
-- logged_at records the telling. Backfill: existing rows were in-the-moment.

ALTER TABLE state_logs ADD COLUMN IF NOT EXISTS felt_at TIMESTAMPTZ;
UPDATE state_logs SET felt_at = logged_at WHERE felt_at IS NULL;
ALTER TABLE state_logs ALTER COLUMN felt_at SET NOT NULL;
