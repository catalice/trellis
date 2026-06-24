-- Reset training data for fresh onboarding.
-- Keeps: trellis_users, all garmin_*, health_*, tasks, captures, ideas, goals, insights, cycle data.
-- Wipes: all training, plans, arc, anchors, profile, context, conversations, readiness scores.

BEGIN;

-- Training sessions (children first to respect FK order)
DELETE FROM training_completions;
DELETE FROM training_session_blocks;
DELETE FROM training_sessions;
DELETE FROM training_plans;
DELETE FROM training_phases;
DELETE FROM training_goals;

-- Goals (app-level, recreated during onboarding)
DELETE FROM goals;

-- Arc and anchors
DELETE FROM training_arcs;
DELETE FROM training_anchors;

-- Workout logs
DELETE FROM workout_checkins;
DELETE FROM strength_sessions;

-- Profile and context
DELETE FROM user_profile;
DELETE FROM current_context;

-- Conversation history and derived readiness
DELETE FROM conversation_turns;
DELETE FROM readiness_observations;

COMMIT;
