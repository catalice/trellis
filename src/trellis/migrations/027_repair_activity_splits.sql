-- Laps arrive from Garmin as an object ({"lapDTOs": [...]}); the splits column
-- was filled only when the payload was a list, so every lap object was stored
-- as []. The raw payload kept them. Copy them across; touch nothing else.
UPDATE garmin_activity_details
SET splits = raw_data->'splits'
WHERE jsonb_typeof(raw_data->'splits') = 'object'
  AND (splits IS NULL OR splits = '[]'::jsonb);
