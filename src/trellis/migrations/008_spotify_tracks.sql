-- Local cache of the user's Spotify tracks (saved, top, recent, playlists), one
-- row per (user, spotify track). Metadata only — the *meaning* embedding lives in
-- the shared memory_index (entity_kind='track', entity_id=this row's id), so recall
-- and the music companion span tracks just like captures/efforts/seeds. Hence the
-- internal UUID id (memory_index.entity_id is UUID); the Spotify base62 id is
-- stored separately as spotify_id.

CREATE TABLE IF NOT EXISTS spotify_tracks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    spotify_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    artists       JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{ id, name }]
    album_name    TEXT,
    genres        TEXT[] NOT NULL DEFAULT '{}',
    popularity    INTEGER,
    external_url  TEXT,
    preview_url   TEXT,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, spotify_id)
);

CREATE INDEX IF NOT EXISTS spotify_tracks_user_idx ON spotify_tracks(user_id);
