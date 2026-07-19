-- Squashed schema: all 27 migrations combined into one idempotent file.
-- Safe to run on a fresh DB or an existing DB (all statements use IF NOT EXISTS).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Core users
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trellis_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id BIGINT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Europe/Madrid',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Efforts (must come before captures so the FK can reference it)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS efforts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    intensity TEXT NOT NULL DEFAULT 'simmering'
        CHECK (intensity IN ('active', 'simmering', 'dormant', 'future')),
    notes TEXT,
    obsidian_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS efforts_user_intensity_idx
    ON efforts(user_id, intensity);

-- ---------------------------------------------------------------------------
-- Captures (must come before tasks so the FK can reference it)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS captures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    raw TEXT NOT NULL,
    capture_type TEXT NOT NULL DEFAULT 'brain_dump'
        CHECK (capture_type IN ('brain_dump', 'idea', 'task', 'question', 'reference')),
    synthesis TEXT,
    summary TEXT,
    effort_id UUID REFERENCES efforts(id) ON DELETE SET NULL,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS captures_user_created_idx
    ON captures(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS captures_user_unassigned_idx
    ON captures(user_id, created_at)
    WHERE effort_id IS NULL AND archived = FALSE;

-- ---------------------------------------------------------------------------
-- Tasks & related
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'done', 'dropped', 'archived')),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high')),
    energy TEXT NOT NULL DEFAULT 'medium'
        CHECK (energy IN ('low', 'medium', 'high')),
    due_at TIMESTAMPTZ,
    source_capture_id UUID REFERENCES captures(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tasks_user_status_idx
    ON tasks(user_id, status, due_at, created_at);

CREATE TABLE IF NOT EXISTS task_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    reason TEXT,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS task_events_user_time_idx
    ON task_events(user_id, occurred_at DESC);

-- reminders.task_id is nullable (standalone reminders allowed)
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    label TEXT,
    remind_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'sent', 'cancelled')),
    recur_daily BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Training
-- ---------------------------------------------------------------------------

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
        CHECK (kind IN ('strength', 'social_run', 'hard_run', 'easy_run', 'long_run', 'mobility')),
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
    session_id UUID NOT NULL UNIQUE REFERENCES training_sessions(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS training_arcs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    goal_id UUID,
    phases JSONB NOT NULL DEFAULT '[]',
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_arcs_user_id_idx ON training_arcs(user_id);

CREATE TABLE IF NOT EXISTS training_anchors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    time_of_day TIME,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    is_hard_constraint BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_anchors_user_id_idx ON training_anchors(user_id);

-- ---------------------------------------------------------------------------
-- Body / readiness / cycle
-- ---------------------------------------------------------------------------

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

CREATE TABLE IF NOT EXISTS cycle_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('period_start', 'observation')),
    occurred_on DATE NOT NULL,
    note TEXT,
    symptoms JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cycle_events_user_occurred_idx
    ON cycle_events(user_id, occurred_on DESC, created_at DESC);

-- ---------------------------------------------------------------------------
-- Health sync & Garmin data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS health_sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'garmin'
        CHECK (provider IN ('garmin', 'self_report')),
    sync_kind TEXT NOT NULL
        CHECK (sync_kind IN ('daily_health', 'activities', 'activity_details')),
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

CREATE TABLE IF NOT EXISTS garmin_activity_details (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    activity_id TEXT NOT NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    splits JSONB NOT NULL DEFAULT '[]'::jsonb,
    split_summaries JSONB NOT NULL DEFAULT '{}'::jsonb,
    typed_splits JSONB NOT NULL DEFAULT '{}'::jsonb,
    exercise_sets JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_run_id UUID REFERENCES health_sync_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, activity_id)
);

CREATE INDEX IF NOT EXISTS garmin_activity_details_user_activity_idx
    ON garmin_activity_details(user_id, activity_id);

CREATE TABLE IF NOT EXISTS garmin_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    email_encrypted TEXT NOT NULL,
    session_dump_encrypted TEXT,
    is_connected BOOLEAN NOT NULL DEFAULT false,
    sync_enabled BOOLEAN NOT NULL DEFAULT true,
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS garmin_connections_connected_idx
    ON garmin_connections(user_id)
    WHERE is_connected = true;

CREATE TABLE IF NOT EXISTS health_self_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    observed_on DATE NOT NULL,
    energy_score SMALLINT CHECK (energy_score BETWEEN 1 AND 10),
    life_load_score SMALLINT CHECK (life_load_score BETWEEN 1 AND 10),
    sleep_minutes INTEGER CHECK (sleep_minutes >= 0),
    body_score SMALLINT CHECK (body_score BETWEEN 1 AND 10),
    soreness_score SMALLINT CHECK (soreness_score BETWEEN 1 AND 10),
    note TEXT,
    source_capture_id UUID REFERENCES captures(id) ON DELETE SET NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS health_self_reports_user_date_idx
    ON health_self_reports(user_id, observed_on DESC, reported_at DESC);

-- ---------------------------------------------------------------------------
-- Session completions & training logs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session_completions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL,
    session_id UUID NOT NULL,
    garmin_activity_id BIGINT,
    session_kind TEXT NOT NULL,
    planned_on DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, plan_id, session_id)
);

CREATE INDEX IF NOT EXISTS session_completions_user_week
    ON session_completions(user_id, planned_on);

CREATE TABLE IF NOT EXISTS workout_checkins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    session_kind TEXT NOT NULL,
    checked_in_on DATE NOT NULL,
    perceived_effort INT CHECK (perceived_effort BETWEEN 1 AND 10),
    feel_note TEXT,
    soreness_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS workout_checkins_user_date
    ON workout_checkins(user_id, checked_in_on);

CREATE TABLE IF NOT EXISTS strength_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    program_phase TEXT,
    exercises JSONB NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS strength_sessions_user_date
    ON strength_sessions(user_id, session_date DESC);

-- ---------------------------------------------------------------------------
-- Goals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    goal_type TEXT NOT NULL CHECK (goal_type IN ('race', 'aerobic', 'strength', 'life', 'habit', 'general')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'achieved', 'paused', 'dropped')),
    target_date DATE,
    is_fixed_date BOOLEAN NOT NULL DEFAULT FALSE,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS goals_user_status_idx ON goals(user_id, status);

-- ---------------------------------------------------------------------------
-- Intelligence (insights)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_count INT NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    detected_on DATE NOT NULL,
    last_confirmed_on DATE NOT NULL,
    expires_on DATE,
    metadata JSONB NOT NULL DEFAULT '{}',
    dismissed_reason TEXT,
    dismissed_at TIMESTAMPTZ,
    snooze_until DATE
);

CREATE INDEX IF NOT EXISTS insights_user_active
    ON insights(user_id, is_active, last_confirmed_on DESC);

CREATE UNIQUE INDEX IF NOT EXISTS insights_user_type_active
    ON insights(user_id, domain, insight_type)
    WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- User profile & context
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_profile (
    user_id UUID PRIMARY KEY,
    name TEXT,
    physical_notes TEXT,
    cognitive_notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS current_context (
    user_id UUID PRIMARY KEY,
    physical_notes TEXT,
    cognitive_notes TEXT,
    misc_notes TEXT,
    valid_until DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, domain)
);

-- ---------------------------------------------------------------------------
-- Conversation history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversation_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS conversation_turns_user_created_idx
    ON conversation_turns(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    domain VARCHAR(50) NOT NULL,
    summary TEXT NOT NULL,
    turns_covered INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, domain)
);

-- ---------------------------------------------------------------------------
-- Learning
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS learning_threads (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS learning_threads_user_active_idx
    ON learning_threads(user_id) WHERE is_active;

CREATE TABLE IF NOT EXISTS learning_entries (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    thread_id UUID NOT NULL REFERENCES learning_threads(id),
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS learning_entries_thread_created_idx
    ON learning_entries(thread_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Migration tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
