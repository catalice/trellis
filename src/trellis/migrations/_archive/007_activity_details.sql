DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.constraint_column_usage
        WHERE table_name = 'health_sync_runs'
          AND constraint_name = 'health_sync_runs_sync_kind_check'
    ) THEN
        ALTER TABLE health_sync_runs
            DROP CONSTRAINT health_sync_runs_sync_kind_check;
    END IF;
END $$;

ALTER TABLE health_sync_runs
    ADD CONSTRAINT health_sync_runs_sync_kind_check
    CHECK (sync_kind IN ('daily_health', 'activities', 'activity_details'));

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
