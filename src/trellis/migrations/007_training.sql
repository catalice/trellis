-- Training (running) — a generated plan derived from the user's goal, plus the
-- dated sessions it schedules. The plan is Claude's judgment (periodisation);
-- Python only stores it. goal_id + goal_snapshot record what the plan was built
-- from, so a later slice can detect when the goal has moved and offer a re-plan.
-- Only one plan is active per user at a time; building a new one supersedes it.

CREATE TABLE IF NOT EXISTS training_plans (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    goal_id       UUID,                       -- the goals row this derives from (nullable)
    goal_snapshot TEXT NOT NULL DEFAULT '',   -- goal state at build time, for staleness
    rationale     TEXT NOT NULL DEFAULT '',   -- Claude's one-line 'shape of this block'
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'superseded')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_plans_user_active_idx
    ON training_plans(user_id, status);

CREATE TABLE IF NOT EXISTS training_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id             UUID NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    scheduled_date      DATE NOT NULL,
    session_type        TEXT NOT NULL,          -- easy | long | intervals | tempo | recovery | rest
    description         TEXT NOT NULL DEFAULT '',
    planned_distance_km NUMERIC(5, 2),
    planned_duration_min INTEGER,
    status              TEXT NOT NULL DEFAULT 'planned'
                        CHECK (status IN ('planned', 'done', 'skipped')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_sessions_user_date_idx
    ON training_sessions(user_id, scheduled_date);
