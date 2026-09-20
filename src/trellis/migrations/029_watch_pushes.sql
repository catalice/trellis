-- A pushed workout is a DATED SESSION. Trellis records what it pushed for each
-- date, so a correction replaces that day's workout and nothing else — a
-- workout is never identified (or deleted) by its name.
CREATE TABLE IF NOT EXISTS watch_pushes (
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    on_date DATE NOT NULL,
    name TEXT NOT NULL,
    workout_id TEXT NOT NULL,
    pushed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, on_date, name)
);
