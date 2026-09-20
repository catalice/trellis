-- The action record: every attempt a turn makes, written BEFORE the tool runs
-- and closed after. It belongs to the turn, not to the model's account of it —
-- a response that is interrupted, wrong or silent cannot erase what was done.
-- A row nobody closed (the process died mid-action) reads as 'unknown'.
CREATE TABLE IF NOT EXISTS action_log (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('succeeded', 'partial', 'failed', 'unknown')),
    summary TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS action_log_user_time ON action_log (user_id, started_at DESC);
