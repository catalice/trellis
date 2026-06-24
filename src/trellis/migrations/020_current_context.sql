CREATE TABLE current_context (
    user_id UUID PRIMARY KEY,
    physical_notes TEXT,
    cognitive_notes TEXT,
    valid_until DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
