CREATE TABLE IF NOT EXISTS captures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text'
        CHECK (content_type IN ('text', 'voice')),
    synthesis TEXT,
    observations JSONB NOT NULL DEFAULT '[]'::jsonb,
    questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    decisions JSONB NOT NULL DEFAULT '[]'::jsonb,
    processing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (processing_status IN ('pending', 'processed', 'failed')),
    processing_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS captures_user_created_idx
    ON captures(user_id, created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'tasks_source_capture_fk'
    ) THEN
        ALTER TABLE tasks
            ADD CONSTRAINT tasks_source_capture_fk
            FOREIGN KEY (source_capture_id)
            REFERENCES captures(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS ideas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    synthesis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'inbox'
        CHECK (status IN ('inbox', 'incubating', 'active', 'archived')),
    source_capture_id UUID REFERENCES captures(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ideas_user_status_idx
    ON ideas(user_id, status, created_at DESC);
