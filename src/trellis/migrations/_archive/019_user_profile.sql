CREATE TABLE user_profile (
    user_id UUID PRIMARY KEY,
    physical_notes TEXT,
    cognitive_notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
