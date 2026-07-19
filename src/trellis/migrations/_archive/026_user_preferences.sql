CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     UUID        NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain      TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, domain)
);
