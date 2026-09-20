-- A change to the training plan that Trellis itself proposes is HELD here until
-- the person agrees to it. The plan used to be stored in the same turn it was
-- first suggested — and stored again, differently, after they objected.
-- Agreement stores THIS record's week, not whatever is composed after the yes.
--   open       -> shown, waiting for their answer (at most one per person)
--   agreed     -> their yes; its week was merged into the stored plan
--   superseded -> replaced by a newer proposal before it was answered
--   withdrawn  -> they said no, or it no longer applies
CREATE TABLE IF NOT EXISTS plan_proposals (
    id           UUID PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    plan         JSONB NOT NULL,
    replace_week BOOLEAN NOT NULL DEFAULT FALSE,
    status       TEXT NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'agreed', 'superseded', 'withdrawn')),
    created_at   TIMESTAMPTZ NOT NULL,
    resolved_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS plan_proposals_one_open ON plan_proposals (user_id) WHERE status = 'open';
