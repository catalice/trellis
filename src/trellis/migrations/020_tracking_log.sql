-- ONE tracking log (her design, 30 Aug): a row is one entry — when, their
-- words, and a FACTS map. A new trackable dimension (anxiety, cramps, focus,
-- restless legs...) is a new key in the map: data, never schema. state_logs
-- and tracking_events fold in; the old views (states = rows with feeling
-- facts/words, events = rows with meds/sleep/period facts) are derived by
-- the repo, not stored.
CREATE TABLE IF NOT EXISTS tracking_log (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id),
    felt_at TIMESTAMPTZ NOT NULL,       -- when it was true (may be retro)
    logged_at TIMESTAMPTZ NOT NULL,     -- when it was told to Trellis
    words TEXT,                          -- their words, verbatim
    facts JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS tracking_log_user_felt ON tracking_log (user_id, felt_at);
CREATE INDEX IF NOT EXISTS tracking_log_facts ON tracking_log USING gin (facts);

-- states: mood/energy become facts, note becomes words
INSERT INTO tracking_log (id, user_id, felt_at, logged_at, words, facts)
SELECT id, user_id, felt_at, logged_at, note,
       jsonb_strip_nulls(jsonb_build_object('mood', mood, 'energy', energy))
FROM state_logs;

-- meds events: the med (name/dose) is the fact
INSERT INTO tracking_log (id, user_id, felt_at, logged_at, words, facts)
SELECT id, user_id, occurred_at, occurred_at, NULL,
       jsonb_build_object('meds', coalesce(detail, 'meds'))
FROM tracking_events WHERE event_type = 'meds';

-- sleep events: hours are the fact, quality words are words
INSERT INTO tracking_log (id, user_id, felt_at, logged_at, words, facts)
SELECT id, user_id, occurred_at, occurred_at, detail,
       jsonb_strip_nulls(jsonb_build_object('sleep_hours', value, 'sleep', true))
FROM tracking_events WHERE event_type = 'sleep';

-- period events: started/ended is the fact
INSERT INTO tracking_log (id, user_id, felt_at, logged_at, words, facts)
SELECT id, user_id, occurred_at, occurred_at, detail,
       jsonb_build_object('period',
           CASE WHEN event_type = 'period_start' THEN 'started' ELSE 'ended' END)
FROM tracking_events WHERE event_type IN ('period_start', 'period_end');

DROP TABLE IF EXISTS tracking_events;
DROP TABLE IF EXISTS state_logs;
