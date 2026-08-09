-- The Watcher (intelligence layer) — the big brain's slow mind.
-- One table: every pattern hypothesis the Watcher has ever entertained.
-- NO seeds are planted here or anywhere: rows are born only from discovery
-- (her rule — no enumeration; the Watcher notices, it is never told what to watch).
CREATE TABLE IF NOT EXISTS watcher_patterns (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id),
    hypothesis TEXT NOT NULL,
    test_spec JSONB,                -- machine-checkable test; NULL = not yet testable
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'verified', 'adopted', 'dismissed', 'watching')),
    evidence TEXT,                  -- human-readable result of the last verification
    stats JSONB,                    -- raw numbers behind the evidence
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,        -- when she adopted/dismissed it
    resolution_note TEXT
);

CREATE INDEX IF NOT EXISTS watcher_patterns_user_status_idx
    ON watcher_patterns(user_id, status);
