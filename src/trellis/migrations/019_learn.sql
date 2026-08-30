-- The Learn house: deliberate understanding. A THREAD is a topic the user is
-- building bottom-up; its map is drawn BY THEM (regions are their labels),
-- Trellis surveys it. Entries carry the material; source-in-truth applies —
-- anything kept as a reference must carry the source it was fetched from.
CREATE TABLE IF NOT EXISTS learn_threads (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id),
    title TEXT NOT NULL,
    position TEXT,                       -- "you are here", in plain words
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS learn_threads_user_title
    ON learn_threads (user_id, lower(title));

CREATE TABLE IF NOT EXISTS learn_entries (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES trellis_users(id),
    thread_id UUID NOT NULL REFERENCES learn_threads(id),
    kind TEXT NOT NULL,                  -- material | source | test
    region TEXT,                         -- where THEY placed it on the map
    content TEXT NOT NULL,
    source_url TEXT,                     -- required when kind='source'
    source_title TEXT,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS learn_entries_thread ON learn_entries (thread_id, created_at);
