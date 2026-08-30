CREATE TABLE IF NOT EXISTS garmin_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    email_encrypted TEXT NOT NULL,
    session_dump_encrypted TEXT,
    is_connected BOOLEAN NOT NULL DEFAULT false,
    sync_enabled BOOLEAN NOT NULL DEFAULT true,
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS garmin_connections_connected_idx
    ON garmin_connections(user_id)
    WHERE is_connected = true;
