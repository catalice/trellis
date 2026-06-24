CREATE TABLE training_anchors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    time_of_day TIME,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    is_hard_constraint BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX training_anchors_user_id_idx ON training_anchors (user_id);
