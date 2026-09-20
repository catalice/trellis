-- health_self_reports was the old health system's twin of what tracking_events
-- does, with no writer since the June rebuild. One source of truth per fact:
-- a dead parallel store gets dropped — but only if it is actually empty. An
-- install that did write to it keeps its rows, readable, under a legacy name.
DO $$
DECLARE
    has_rows boolean;
BEGIN
    IF to_regclass('health_self_reports') IS NULL THEN
        RETURN;
    END IF;
    EXECUTE 'SELECT EXISTS (SELECT 1 FROM health_self_reports)' INTO has_rows;
    IF has_rows THEN
        ALTER TABLE health_self_reports RENAME TO health_self_reports_legacy;
        RAISE NOTICE 'health_self_reports kept as health_self_reports_legacy: it was not empty';
    ELSE
        DROP TABLE health_self_reports;
    END IF;
END $$;
