-- The logbook restructure: one activities store, the user's words beside it.
--
-- garmin_activities gains user_note — the user's layer ("social run", "bailed
-- early, knee"). Sync's upsert names its columns and never this one, so a
-- resync structurally cannot touch their words.
--
-- training_runs (the old runs-only side table) is folded in: its notes were
-- composed as "<garmin name>[ (avg HR n)][ — <their words>]"; only the words
-- that aren't derivable from the activity row are carried over. Rows were
-- matched to activities by garmin id (or by date for pre-link rows) before
-- this migration ships; the table is then dropped.

ALTER TABLE garmin_activities ADD COLUMN IF NOT EXISTS user_note TEXT;

-- 1) id-linked rows
UPDATE garmin_activities ga
SET user_note = NULLIF(
    btrim(
        regexp_replace(
            CASE WHEN position(ga.name IN tr.note) = 1
                 THEN substr(tr.note, length(ga.name) + 1)
                 ELSE tr.note END,
            '^\s*\(avg HR [0-9]+\)', ''),
        ' —–-'),
    '')
FROM training_runs tr
WHERE tr.user_id = ga.user_id
  AND tr.garmin_activity_id = ga.activity_id
  AND tr.note IS NOT NULL
  AND ga.user_note IS NULL;

-- 2) date-matched rows (imported before the garmin id existed)
UPDATE garmin_activities ga
SET user_note = NULLIF(
    btrim(
        regexp_replace(
            CASE WHEN position(ga.name IN tr.note) = 1
                 THEN substr(tr.note, length(ga.name) + 1)
                 ELSE tr.note END,
            '^\s*\(avg HR [0-9]+\)', ''),
        ' —–-'),
    '')
FROM training_runs tr
WHERE tr.user_id = ga.user_id
  AND tr.garmin_activity_id IS NULL
  AND ga.activity_type = 'running'
  AND to_timestamp(ga.start_time_epoch_seconds)::date = tr.ran_on
  AND tr.note IS NOT NULL
  AND ga.user_note IS NULL;

DROP TABLE IF EXISTS training_runs;
