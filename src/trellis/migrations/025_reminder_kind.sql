-- Reminders grow a KIND (her design, 15 Sep 2026): 'remind' posts the label
-- back verbatim (what always happened); 'check_in' wakes the oracle instead —
-- the label is Trellis's instruction, it runs a normal turn and speaks first.
-- Nothing about WHEN or WHAT a check-in covers is coded; the user words it.
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'remind'
    CHECK (kind IN ('remind', 'check_in'));
