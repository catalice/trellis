-- Self-tracking: state logs (energy/mood over the day) and tracking events
-- (meds, sleep, period). Raw note always preserved alongside derived scores.

CREATE TABLE IF NOT EXISTS state_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    energy SMALLINT CHECK (energy BETWEEN 1 AND 5),
    mood SMALLINT CHECK (mood BETWEEN 1 AND 5),
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS state_logs_user_time_idx
    ON state_logs(user_id, logged_at DESC);

CREATE TABLE IF NOT EXISTS tracking_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('meds', 'sleep', 'period_start', 'period_end')),
    detail TEXT,
    value NUMERIC(5, 2),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tracking_events_user_time_idx
    ON tracking_events(user_id, occurred_at DESC);
