-- Rolling the CODE back to stage 1 while keeping the database.
--
-- Image and git tags are not the whole rollback. Stage 1 only looks at reminders
-- whose status is 'scheduled': anything the new code left 'claimed' or
-- 'executed' would sit there for ever, silently missed. Restoring the old
-- database dump instead would throw away everything written since.
--
-- So the rows are reconciled in place — and a check-in's actions are NEVER run
-- again:
--   * a plain reminder goes back to 'scheduled'; stage 1 delivers it (a possible
--     duplicate is preferred to a miss).
--   * a check-in becomes a PLAIN notification carrying what there is to say: its
--     stored reply if its turn had run, else a note that it was interrupted.
--     Stage 1 posts a plain reminder verbatim, with no model — so nothing re-runs.
--     Its recurrence is cleared: the next occurrence was already scheduled when
--     it was claimed.
-- The new tables (action_log, watch_pushes) and columns are harmless to stage 1
-- and are left in place. Run with the bot stopped:
--
--   docker compose stop trellis
--   docker compose exec -T postgres psql -U trellis trellis -v ON_ERROR_STOP=1 < scripts/rollback_stages_2_3.sql
--   (then bring the stage 1 image back up)

BEGIN;

SELECT id, kind, status, left(label, 60) AS label, (message IS NOT NULL) AS has_reply
FROM reminders WHERE status IN ('claimed', 'executed')
ORDER BY remind_at;

UPDATE reminders
SET status = 'scheduled'
WHERE status IN ('claimed', 'executed') AND kind = 'remind';

UPDATE reminders
SET kind = 'remind',
    status = 'scheduled',
    recurrence = NULL,
    label = COALESCE(
        NULLIF(btrim(message), ''),
        'A check-in was due (' || label || ') but was interrupted by a rollback. '
        || 'Some of it may have happened — it has not been run again.')
WHERE status IN ('claimed', 'executed') AND kind = 'check_in';

COMMIT;
