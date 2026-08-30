CREATE TABLE IF NOT EXISTS training_goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    distance_km NUMERIC(6, 3) NOT NULL CHECK (distance_km > 0),
    target TEXT NOT NULL DEFAULT 'complete',
    stretch_time_minutes INTEGER CHECK (stretch_time_minutes > 0),
    target_event_date DATE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'achieved', 'paused', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_goals_user_status_idx
    ON training_goals(user_id, status);

CREATE TABLE IF NOT EXISTS training_phases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES training_goals(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phase_type TEXT NOT NULL
        CHECK (phase_type IN ('base', 'build', 'specific', 'taper', 'recovery')),
    starts_on DATE NOT NULL,
    ends_on DATE NOT NULL,
    target_runs_per_week INTEGER NOT NULL CHECK (target_runs_per_week BETWEEN 1 AND 7),
    long_run_minutes INTEGER NOT NULL CHECK (long_run_minutes > 0),
    plan_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_on >= starts_on)
);

CREATE TABLE IF NOT EXISTS training_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    goal_id UUID REFERENCES training_goals(id) ON DELETE SET NULL,
    phase_id UUID REFERENCES training_phases(id) ON DELETE SET NULL,
    week_start DATE NOT NULL,
    mode TEXT NOT NULL DEFAULT 'build'
        CHECK (mode IN ('build', 'deload', 'holiday')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('draft', 'active', 'superseded', 'completed')),
    rationale JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, week_start, revision)
);

CREATE INDEX IF NOT EXISTS training_plans_user_week_idx
    ON training_plans(user_id, week_start DESC, revision DESC);

CREATE TABLE IF NOT EXISTS training_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ,
    scheduled_day SMALLINT NOT NULL CHECK (scheduled_day BETWEEN 0 AND 6),
    kind TEXT NOT NULL
        CHECK (kind IN (
            'strength', 'social_run', 'hard_run', 'easy_run', 'long_run', 'mobility'
        )),
    title TEXT NOT NULL,
    intensity TEXT NOT NULL CHECK (intensity IN ('easy', 'moderate', 'hard')),
    planned_duration_minutes INTEGER NOT NULL CHECK (planned_duration_minutes > 0),
    fixed_anchor BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'completed', 'missed', 'declined', 'cancelled')),
    replaces_session_id UUID REFERENCES training_sessions(id) ON DELETE SET NULL,
    notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_sessions_user_schedule_idx
    ON training_sessions(user_id, scheduled_for, scheduled_day);

CREATE TABLE IF NOT EXISTS training_session_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL CHECK (position >= 0),
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    instructions JSONB NOT NULL,
    UNIQUE (session_id, position)
);

CREATE TABLE IF NOT EXISTS training_completions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE
        REFERENCES training_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    completed_at TIMESTAMPTZ NOT NULL,
    actual_duration_minutes INTEGER CHECK (actual_duration_minutes > 0),
    perceived_effort SMALLINT CHECK (perceived_effort BETWEEN 1 AND 10),
    activation_completed BOOLEAN,
    cooldown_completed BOOLEAN,
    pain_or_niggle_note TEXT,
    user_notes TEXT,
    source_activity_id TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_completions_user_time_idx
    ON training_completions(user_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS readiness_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    observed_on DATE NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('garmin', 'self_report', 'combined')),
    readiness_score SMALLINT CHECK (readiness_score BETWEEN 0 AND 100),
    sleep_minutes INTEGER CHECK (sleep_minutes >= 0),
    body_battery SMALLINT CHECK (body_battery BETWEEN 0 AND 100),
    resting_heart_rate SMALLINT CHECK (resting_heart_rate > 0),
    hrv_ms NUMERIC(7, 2) CHECK (hrv_ms > 0),
    energy_score SMALLINT CHECK (energy_score BETWEEN 1 AND 10),
    life_load_score SMALLINT CHECK (life_load_score BETWEEN 1 AND 10),
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, observed_on, source)
);

CREATE TABLE IF NOT EXISTS cycle_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    observed_on DATE NOT NULL,
    observation_type TEXT NOT NULL
        CHECK (observation_type IN ('period_start', 'period_end', 'symptom', 'note')),
    symptom TEXT,
    severity SMALLINT CHECK (severity BETWEEN 1 AND 10),
    note TEXT,
    source TEXT NOT NULL DEFAULT 'self_report',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cycle_observations_user_date_idx
    ON cycle_observations(user_id, observed_on DESC);
