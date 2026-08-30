CREATE TABLE IF NOT EXISTS cycle_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('period_start', 'observation')),
    occurred_on DATE NOT NULL,
    note TEXT,
    symptoms JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cycle_events_user_occurred_idx
    ON cycle_events(user_id, occurred_on DESC, created_at DESC);
