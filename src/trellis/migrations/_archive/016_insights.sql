CREATE TABLE IF NOT EXISTS insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_count INT NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    detected_on DATE NOT NULL,
    last_confirmed_on DATE NOT NULL,
    expires_on DATE,
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS insights_user_active ON insights(user_id, is_active, last_confirmed_on DESC);

CREATE UNIQUE INDEX IF NOT EXISTS insights_user_type_active
    ON insights(user_id, domain, insight_type)
    WHERE is_active = TRUE;
