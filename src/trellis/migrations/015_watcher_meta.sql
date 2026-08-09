-- Discovery cadence marker, independent of whether anything was proposed:
-- "propose nothing" is a valid weekly outcome and must still advance the
-- clock (MAX(proposed_at) leaked into daily discovery calls on quiet weeks).
CREATE TABLE IF NOT EXISTS watcher_meta (
    user_id UUID PRIMARY KEY REFERENCES trellis_users(id),
    last_discovery_at TIMESTAMPTZ
);
