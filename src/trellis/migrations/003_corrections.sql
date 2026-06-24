CREATE TABLE IF NOT EXISTS correction_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    instruction TEXT NOT NULL,
    action_type TEXT NOT NULL,
    affected_task_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    affected_idea_id UUID REFERENCES ideas(id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS correction_events_user_created_idx
    ON correction_events(user_id, created_at DESC);
