-- Trellis second brain schema — one idempotent file, only what the live product uses.
-- Safe to run on a fresh DB or an existing DB (all statements use IF NOT EXISTS).
-- Module tables (training, learn, tracking) arrive with their own phases.

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
-- Self-tracking (state logs + meds/sleep/period events)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS state_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    energy SMALLINT CHECK (energy BETWEEN 1 AND 5),
    mood SMALLINT CHECK (mood BETWEEN 1 AND 5),
    felt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS state_logs_user_time_idx
    ON state_logs(user_id, logged_at DESC);

CREATE TABLE IF NOT EXISTS tracking_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES trellis_users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('meds', 'sleep', 'period_start', 'period_end')),
    detail TEXT,
    value NUMERIC(5, 2),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tracking_events_user_time_idx
    ON tracking_events(user_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Migration tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
