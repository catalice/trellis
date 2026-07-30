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
