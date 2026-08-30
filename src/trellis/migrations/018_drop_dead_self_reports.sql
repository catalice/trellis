-- health_self_reports was the old health system's twin of what tracking_events
-- does — zero rows, no writer since the June rebuild. One source of truth per
-- fact: dead parallel stores get dropped, not kept "just in case".
DROP TABLE IF EXISTS health_self_reports;
