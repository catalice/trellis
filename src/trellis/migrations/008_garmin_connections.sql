-- Garmin connection per user — the encrypted session that lets the coach push
-- workouts to the watch and read recent runs. Credentials are encrypted at rest
-- with pgcrypto (pgp_sym_encrypt, keyed by TRELLIS_SECRET_KEY); only the derived
-- flags/status are stored in clear. Created by the trellis-garmin-setup flow;
-- read by GarminDirectService (push) and GarminActivityReader (recent runs).

CREATE TABLE IF NOT EXISTS garmin_connections (
    user_id                UUID PRIMARY KEY REFERENCES trellis_users(id) ON DELETE CASCADE,
    email_encrypted        TEXT,
    session_dump_encrypted TEXT,
    is_connected           BOOLEAN NOT NULL DEFAULT FALSE,
    sync_enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync_at           TIMESTAMPTZ,
    last_error             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
