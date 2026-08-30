CREATE TABLE IF NOT EXISTS strength_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    program_phase TEXT,
    exercises JSONB NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS strength_sessions_user_date ON strength_sessions(user_id, session_date DESC);
