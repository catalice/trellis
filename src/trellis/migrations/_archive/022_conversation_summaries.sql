CREATE TABLE IF NOT EXISTS conversation_summaries (
    user_id       UUID         NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain        VARCHAR(50)  NOT NULL,
    summary       TEXT         NOT NULL,
    turns_covered INTEGER      NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, domain)
);
