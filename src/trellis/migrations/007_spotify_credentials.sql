-- Spotify credentials for the music domain — one row per user. The access token
-- is short-lived and refreshed via the refresh token; both live in the local DB
-- alongside the app's other secrets. connected_at is the first link; updated_at
-- moves on every refresh.

CREATE TABLE IF NOT EXISTS spotify_credentials (
    user_id       UUID PRIMARY KEY REFERENCES trellis_users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT '',
    expires_at    TIMESTAMPTZ NOT NULL,
    connected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
