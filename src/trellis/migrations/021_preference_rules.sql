-- Preferences become ROWS (her call, 2 Sep): one rule, one row, one id —
-- reviewable, updatable, removable individually. The July blob (one text
-- field per domain) couldn't be surgically edited; append-by-default was a
-- patch on its data-loss bug, not a shape fix. Blob lines migrate 1:1
-- (each appended preference was one line).
CREATE TABLE IF NOT EXISTS preference_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id),
    domain TEXT NOT NULL,
    rule TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS preference_rules_user_domain
    ON preference_rules (user_id, domain);

INSERT INTO preference_rules (user_id, domain, rule)
SELECT user_id, domain, trim(line)
FROM user_preferences, regexp_split_to_table(content, E'\n') AS line
WHERE trim(line) <> '';

DROP TABLE IF EXISTS user_preferences;
