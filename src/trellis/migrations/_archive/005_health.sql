CREATE TABLE IF NOT EXISTS health_sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'garmin'
        CHECK (provider IN ('garmin', 'self_report')),
    sync_kind TEXT NOT NULL
        CHECK (sync_kind IN ('daily_health', 'activities')),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    start_date DATE,
    end_date DATE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    records_upserted INTEGER NOT NULL DEFAULT 0 CHECK (records_upserted >= 0),
    error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS health_sync_runs_user_started_idx
    ON health_sync_runs(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS garmin_daily_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    observed_on DATE NOT NULL,
    steps INTEGER CHECK (steps >= 0),
    calories INTEGER CHECK (calories >= 0),
    distance_meters NUMERIC(12, 2) CHECK (distance_meters >= 0),
    active_minutes INTEGER CHECK (active_minutes >= 0),
    resting_heart_rate SMALLINT CHECK (resting_heart_rate > 0),
    average_heart_rate SMALLINT CHECK (average_heart_rate > 0),
    maximum_heart_rate SMALLINT CHECK (maximum_heart_rate > 0),
    sleep_duration_minutes INTEGER CHECK (sleep_duration_minutes >= 0),
    sleep_score SMALLINT CHECK (sleep_score BETWEEN 0 AND 100),
    body_battery_maximum SMALLINT CHECK (body_battery_maximum BETWEEN 0 AND 100),
    body_battery_minimum SMALLINT CHECK (body_battery_minimum BETWEEN 0 AND 100),
    body_battery_end SMALLINT CHECK (body_battery_end BETWEEN 0 AND 100),
    average_stress SMALLINT CHECK (average_stress BETWEEN 0 AND 100),
    hrv_weekly_average NUMERIC(7, 2) CHECK (hrv_weekly_average > 0),
    hrv_last_night NUMERIC(7, 2) CHECK (hrv_last_night > 0),
    hrv_status TEXT,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_run_id UUID REFERENCES health_sync_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, observed_on)
);

CREATE INDEX IF NOT EXISTS garmin_daily_health_user_date_idx
    ON garmin_daily_health(user_id, observed_on DESC);

CREATE TABLE IF NOT EXISTS garmin_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    activity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    start_time_epoch_seconds BIGINT,
    duration_milliseconds NUMERIC(14, 2) CHECK (duration_milliseconds >= 0),
    calories INTEGER CHECK (calories >= 0),
    average_heart_rate SMALLINT CHECK (average_heart_rate > 0),
    maximum_heart_rate SMALLINT CHECK (maximum_heart_rate > 0),
    distance_meters NUMERIC(12, 2) CHECK (distance_meters >= 0),
    elevation_gain_meters NUMERIC(10, 2),
    elevation_loss_meters NUMERIC(10, 2),
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_run_id UUID REFERENCES health_sync_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, activity_id)
);

CREATE INDEX IF NOT EXISTS garmin_activities_user_start_idx
    ON garmin_activities(user_id, start_time_epoch_seconds DESC);

CREATE TABLE IF NOT EXISTS health_self_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    observed_on DATE NOT NULL,
    energy_score SMALLINT CHECK (energy_score BETWEEN 1 AND 10),
    life_load_score SMALLINT CHECK (life_load_score BETWEEN 1 AND 10),
    sleep_minutes INTEGER CHECK (sleep_minutes >= 0),
    body_score SMALLINT CHECK (body_score BETWEEN 1 AND 10),
    note TEXT,
    source_capture_id UUID REFERENCES captures(id) ON DELETE SET NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS health_self_reports_user_date_idx
    ON health_self_reports(user_id, observed_on DESC, reported_at DESC);
