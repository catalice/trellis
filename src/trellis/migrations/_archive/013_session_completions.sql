CREATE TABLE IF NOT EXISTS session_completions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL,
    session_id UUID NOT NULL,
    garmin_activity_id BIGINT,
    session_kind TEXT NOT NULL,
    planned_on DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, plan_id, session_id)
);
CREATE INDEX IF NOT EXISTS session_completions_user_week ON session_completions(user_id, planned_on);
