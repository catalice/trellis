CREATE TABLE IF NOT EXISTS workout_checkins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    session_kind TEXT NOT NULL,
    checked_in_on DATE NOT NULL,
    perceived_effort INT CHECK (perceived_effort BETWEEN 1 AND 10),
    feel_note TEXT,
    soreness_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS workout_checkins_user_date ON workout_checkins(user_id, checked_in_on);
