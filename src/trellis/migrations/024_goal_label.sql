-- A goal is just a goal (her call, 10 Sep 2026): the goal_type enum becomes
-- an OPTIONAL free-text label. Existing values ('race', 'life', ...) survive
-- as labels; 'race'/'aerobic'/'strength' still feed the coach, by convention
-- instead of constraint.
--
-- Conditional: an existing install renames its goal_type column; a fresh
-- install already got `label` from 001 and this is a no-op. The CHECK is
-- dropped by inspection, not by guessed name — an IF EXISTS on the wrong
-- name would "succeed" while leaving the enum enforced.
DO $$
DECLARE c RECORD;
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'goals' AND column_name = 'goal_type'
  ) THEN
    FOR c IN
      SELECT conname FROM pg_constraint
      WHERE conrelid = 'goals'::regclass AND contype = 'c'
        AND pg_get_constraintdef(oid) ILIKE '%goal_type%'
    LOOP
      EXECUTE format('ALTER TABLE goals DROP CONSTRAINT %I', c.conname);
    END LOOP;
    ALTER TABLE goals ALTER COLUMN goal_type DROP NOT NULL;
    ALTER TABLE goals RENAME COLUMN goal_type TO label;
  END IF;
END $$;
