-- Life context becomes a short dated log (the user's design, 20 Sep 2026): one line per
-- entry, each with the day it was said and its own expiry. The old three-field
-- blob was rewritten wholesale by the model and loaded every turn as if true.
-- Python stamps the date and enforces the shape; the model only supplies the line.
CREATE TABLE IF NOT EXISTS context_entries (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    said_on DATE NOT NULL,
    expires_on DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS context_entries_live ON context_entries (user_id, expires_on);

-- Carry any text the blob still holds, one entry per field, dates kept.
INSERT INTO context_entries (id, user_id, text, said_on, expires_on)
SELECT gen_random_uuid(), c.user_id, btrim(f.val), c.updated_at::date, c.valid_until
FROM current_context c
CROSS JOIN LATERAL (VALUES (c.misc_notes), (c.physical_notes), (c.cognitive_notes)) AS f(val)
WHERE btrim(coalesce(f.val, '')) <> ''
  AND EXISTS (SELECT 1 FROM trellis_users u WHERE u.id = c.user_id);

DROP TABLE current_context;
