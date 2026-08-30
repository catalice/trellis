-- Training (running) coach. The coach IS Claude — it reads the goal + baseline +
-- the real calendar and AUTHORS/adapts the plan in conversation; Python only
-- stores it. One row per user: the plan (a JSON doc Claude maintains — a rough arc
-- toward the goal + the current week's dated sessions) and an optional baseline
-- summary (read from Garmin data or a quick conversation). The goal itself lives
-- in the goals table (second brain); this only references it.

CREATE TABLE IF NOT EXISTS training_plan (
    user_id    UUID PRIMARY KEY REFERENCES trellis_users(id) ON DELETE CASCADE,
    goal_id    UUID,                                -- goals row this works toward (nullable)
    baseline   TEXT,                                -- Claude's baseline read (data or conversation)
    plan       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- Claude-authored: rough arc + current week
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Completed runs — the coach plans the next run from the last ones. Lean: date,
-- plain-words note, optional distance. Recorded when the user says they ran.
CREATE TABLE IF NOT EXISTS training_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    ran_on      DATE NOT NULL,
    note        TEXT NOT NULL,
    distance_km NUMERIC(6, 2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_runs_user_date_idx
    ON training_runs(user_id, ran_on DESC);
