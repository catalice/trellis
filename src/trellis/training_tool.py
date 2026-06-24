"""
Tool schemas and handlers for the training domain.

All tools follow the assembler handler signature: (user_id, input_dict, now) -> str.
Register with: registry.add_domain("training", ..., training_tools(...), TRAINING_SIGNALS)
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session parsing helpers (used by save_week_plan and apply_readiness_adaptation)
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES: dict[str, Any] = {}  # populated lazily to avoid circular import


def _weekday_map():
    from trellis.training import Weekday
    return {
        "monday": Weekday.MONDAY, "tuesday": Weekday.TUESDAY,
        "wednesday": Weekday.WEDNESDAY, "thursday": Weekday.THURSDAY,
        "friday": Weekday.FRIDAY, "saturday": Weekday.SATURDAY, "sunday": Weekday.SUNDAY,
    }


def _parse_weekday(name: Any):
    if not name:
        return None
    return _weekday_map().get(str(name).lower().strip())


def _parse_claude_session(raw: Any):
    if not isinstance(raw, dict):
        return None
    try:
        from trellis.training import Intensity, SessionBlock, SessionKind, TrainingSession, Weekday
        day = _parse_weekday(raw.get("day"))
        if day is None:
            return None
        kind = SessionKind(str(raw.get("kind", "")))
        intensity = Intensity(str(raw.get("intensity", "easy")))
        title = str(raw.get("title", kind.value.replace("_", " ").title())).strip() or kind.value.replace("_", " ").title()
        blocks = []
        for b in raw.get("blocks", []):
            if not isinstance(b, dict):
                continue
            name = str(b.get("name", "")).strip()
            duration = int(b.get("duration_minutes", 0))
            instructions = tuple(str(i).strip() for i in b.get("instructions", []) if str(i).strip())
            if name and duration > 0 and instructions:
                blocks.append(SessionBlock(name, duration, instructions))
        if not blocks:
            return None
        notes = tuple(str(n).strip() for n in raw.get("notes", []) if str(n).strip())
        return TrainingSession(id=uuid4(), day=day, kind=kind, title=title,
                               intensity=intensity, blocks=tuple(blocks), notes=notes)
    except (ValueError, KeyError, TypeError):
        _log.warning("_parse_claude_session: could not parse: %r", raw)
        return None


def _parse_claude_sessions(raw_list: Any) -> tuple:
    if not isinstance(raw_list, list):
        return ()
    result = []
    seen: set = set()
    for raw in raw_list:
        session = _parse_claude_session(raw)
        if session is not None and session.day not in seen:
            result.append(session)
            seen.add(session.day)
    return tuple(result)


def _format_arc_for_display(arc, today: date) -> str:
    lines = ["Your training arc:\n"]
    for phase in arc.phases:
        is_current = phase.start_date <= today <= phase.end_date
        marker = " ← YOU ARE HERE" if is_current else ""
        week_info = arc.phase_week(today) if is_current else None
        week_str = f" (week {week_info[0]} of {week_info[1]})" if week_info else ""
        lines.append(
            f"{'▶' if is_current else '·'} {phase.name}{marker}{week_str}"
            f"\n  {phase.start_date.strftime('%d %b')} – {phase.end_date.strftime('%d %b %Y')}"
            f"\n  {phase.focus}"
            f"\n  {phase.weekly_runs} runs/week · long run to {phase.long_run_minutes}m · {phase.intensity}"
        )
        if phase.notes:
            lines.append(f"  ⚑ {phase.notes}")
        lines.append("")
    return "\n".join(lines).strip()


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _target_week_start(day: date) -> date:
    start = _week_start(day)
    if day.weekday() == 6:  # Sunday → plan next week
        return start + timedelta(days=7)
    return start


def _parse_date_iso(value: str | None, fallback: date) -> tuple[date, str | None]:
    if not value:
        return fallback, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return fallback, f"Could not parse date '{value}'. Use YYYY-MM-DD format."


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

GET_TRAINING_PLAN_TOOL = {
    "name": "get_training_plan",
    "description": "Get the current week's training plan.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

GET_SESSION_DETAIL_TOOL = {
    "name": "get_session_detail",
    "description": "Get full activation, run and cooldown instructions for one session.",
    "input_schema": {
        "type": "object",
        "properties": {
            "session_kind": {
                "type": "string",
                "enum": ["hard_run", "easy_run", "long_run", "social_run", "mobility", "strength"],
            }
        },
        "required": ["session_kind"],
    },
}

ADJUST_TRAINING_TOOL = {
    "name": "adjust_training",
    "description": (
        "Change or query the training plan. Use for: creating a new plan, "
        "replacing the social run, holiday or deload week, "
        "explaining the plan rationale, or showing what's on today's schedule. "
        "To change PT days: call set_training_anchor, then create_plan."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["explain_plan", "today_training"],
                "description": "explain_plan: return plan facts for explanation. today_training: return today's session and readiness.",
            },
            "replacement_day": {
                "anyOf": [
                    {"type": "string", "enum": [
                        "monday", "tuesday", "wednesday", "thursday",
                        "friday", "saturday", "sunday",
                    ]},
                    {"type": "null"},
                ]
            },
            "replacement_time_of_day": {
                "anyOf": [
                    {"type": "string", "enum": ["morning", "lunch", "afternoon", "evening"]},
                    {"type": "null"},
                ]
            },
            "run_count": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 7}, {"type": "null"}]
            },
            "strength_days": {
                "type": "array",
                "items": {"type": "string", "enum": [
                    "monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday",
                ]},
            },
        },
        "required": ["action"],
    },
}

SAVE_WEEK_PLAN_TOOL = {
    "name": "save_week_plan",
    "description": (
        "Save the week's training plan. Call this after generating the full sessions list. "
        "Do not generate strength sessions — those come from anchors and are added automatically. "
        "Generates and saves a complete plan replacing any existing one for this week."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["BUILD", "DELOAD"], "description": "BUILD or DELOAD week."},
            "phase": {"type": "string", "enum": ["build", "sharpen", "taper"], "description": "Training phase."},
            "rationale": {"type": "string", "description": "One sentence on why this week looks the way it does."},
            "sessions": {
                "type": "array",
                "description": "Running and mobility sessions for the week. Do not include strength.",
                "items": {
                    "type": "object",
                    "properties": {
                        "day": {"type": "string", "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
                        "kind": {"type": "string", "enum": ["easy_run", "hard_run", "long_run", "social_run", "mobility"]},
                        "title": {"type": "string"},
                        "intensity": {"type": "string", "enum": ["easy", "moderate", "hard"]},
                        "notes": {"type": "array", "items": {"type": "string"}},
                        "blocks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "duration_minutes": {"type": "integer"},
                                    "instructions": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["name", "duration_minutes", "instructions"],
                            },
                        },
                    },
                    "required": ["day", "kind", "title", "intensity", "blocks"],
                },
            },
        },
        "required": ["mode", "phase", "sessions"],
    },
}

SAVE_TRAINING_ARC_TOOL = {
    "name": "save_training_arc",
    "description": (
        "Save a newly generated training arc. Call after generating all phases. "
        "Replaces any existing arc. Build toward race date then include recovery."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Phase name, e.g. 'Aerobic Base', 'Build', 'Sharpen', 'Taper', 'Recovery'."},
                        "focus": {"type": "string", "description": "One sentence on the purpose of this phase."},
                        "start_date": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                        "end_date": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                        "weekly_runs": {"type": "integer"},
                        "long_run_minutes": {"type": "integer"},
                        "intensity": {"type": "string", "enum": ["easy", "mixed", "hard"]},
                        "notes": {"type": "string", "description": "Life events or special considerations. Empty string if none."},
                    },
                    "required": ["name", "focus", "start_date", "end_date", "weekly_runs", "long_run_minutes", "intensity"],
                },
            },
        },
        "required": ["phases"],
    },
}

APPLY_READINESS_ADAPTATION_TOOL = {
    "name": "apply_readiness_adaptation",
    "description": (
        "Apply a readiness-based adaptation to today's training session. "
        "Generate the adapted session content then call this to save it. "
        "Only call when the user has confirmed they want to adapt today's session."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["reduce", "swap", "rest"],
                "description": "reduce: lighter version of same type. swap: replace hard with easy. rest: replace with mobility.",
            },
            "session": {
                "type": "object",
                "description": "The adapted session to replace today's primary session.",
                "properties": {
                    "kind": {"type": "string", "enum": ["easy_run", "hard_run", "long_run", "mobility"]},
                    "title": {"type": "string"},
                    "intensity": {"type": "string", "enum": ["easy", "moderate", "hard"]},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "duration_minutes": {"type": "integer"},
                                "instructions": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name", "duration_minutes", "instructions"],
                        },
                    },
                },
                "required": ["kind", "title", "intensity", "blocks"],
            },
        },
        "required": ["action", "session"],
    },
}

GET_HEALTH_SUMMARY_TOOL = {
    "name": "get_health_summary",
    "description": "Get latest Garmin health data and readiness score.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

RECORD_MORNING_CHECKIN_TOOL = {
    "name": "record_morning_checkin",
    "description": (
        "Record the user's morning self-report. Call this when they send "
        "energy/body/life load scores in the morning. "
        "Include soreness if they mention it; omit (null) if she doesn't."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "energy": {"type": "integer", "minimum": 1, "maximum": 10},
            "body": {"type": "integer", "minimum": 1, "maximum": 10},
            "life_load": {"type": "integer", "minimum": 1, "maximum": 10},
            "soreness": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "Optional soreness score 1-10.",
            },
            "notes": {"type": "string", "description": "Additional notes or context."},
        },
        "required": ["energy", "body", "life_load"],
    },
}

RECORD_POST_WORKOUT_CHECKIN_TOOL = {
    "name": "record_post_workout_checkin",
    "description": (
        "Record how the user felt after a training session — RPE, how it felt, anything sore. "
        "Call this when they mention finishing a workout and describes how she felt. "
        "Do not ask for confirmation before calling."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_kind": {
                "type": "string",
                "enum": ["hard_run", "easy_run", "long_run", "social_run", "mobility", "strength"],
                "description": "The type of session that was completed.",
            },
            "perceived_effort": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "RPE 1-10. Infer from language if not stated explicitly.",
            },
            "feel_note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "How the session felt — energy, legs, general quality. Use her words.",
            },
            "soreness_note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Any specific soreness or tightness mentioned.",
            },
            "session_date_iso": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Date of the session. Defaults to today. Use when logging a past session.",
            },
        },
        "required": ["session_kind"],
    },
}

RECORD_STRENGTH_SESSION_TOOL = {
    "name": "record_strength_session",
    "description": (
        "Record what the user did in a strength/PT session. "
        "Call this when they mention finishing a PT or gym session and describes the exercises. "
        "Parse exercise names, sets, reps, and weight from her message. "
        "Do not ask for confirmation before calling."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "exercises": {
                "type": "array",
                "description": "Exercises done in this session.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "sets": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "reps": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "weight_kg": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                        "duration_seconds": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                    "required": ["name"],
                },
            },
            "session_date_iso": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Date of the session. Defaults to today.",
            },
            "program_phase": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Program phase if mentioned, e.g. 'phase 2'.",
            },
            "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["exercises"],
    },
}

GET_WEEK_COMPLETION_TOOL = {
    "name": "get_week_completion",
    "description": "Show which training sessions in the current week have a matching Garmin activity and which haven't happened yet. Use this mid-week or when Cat asks about this week's progress — not for reviewing last week.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

GET_WEEK_REVIEW_TOOL = {
    "name": "get_week_review",
    "description": (
        "Get a retrospective of last week (the previous Mon–Sun) — "
        "session completion, how each session felt, PT/strength work, and daily body scores. "
        "Call this when Cat says 'sunday', 'monday', 'week review', 'how did last week go', "
        "or anything about reviewing the week that just ended. "
        "After presenting it, ask what next week looks like so the plan can be adapted."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

GET_TRAINING_ARC_TOOL = {
    "name": "get_training_arc",
    "description": (
        "Show the full training arc — all phases from now to race day and beyond, "
        "with current phase highlighted. Call when the user asks about the training plan, "
        "what phase she's in, what's coming up, or the big picture."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

GENERATE_TRAINING_ARC_TOOL = {
    "name": "generate_training_arc",
    "description": (
        "Generate or regenerate the training arc. Claude will ask the user for any missing "
        "context (life events, holidays, preferences) then build a periodized plan "
        "from today to the race date and beyond. Call when the user asks to build, rebuild, "
        "or update the training arc, or when a new race goal is saved."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "context": {
                "type": "string",
                "description": (
                    "Any extra context to inform the arc: life events, holidays, "
                    "wedding date, travel, preferences the user has mentioned."
                ),
            },
        },
        "required": [],
    },
}

LIST_TRAINING_ANCHORS_TOOL = {
    "name": "list_training_anchors",
    "description": "List the user's fixed training commitments (PT, social run, etc.) with their IDs.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

SET_TRAINING_ANCHOR_TOOL = {
    "name": "set_training_anchor",
    "description": (
        "Save a fixed recurring training commitment — PT session, social run, group workout. "
        "Call when the user mentions a regular session that should always be accounted for in the plan."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "day_of_week": {
                "type": "integer", "minimum": 0, "maximum": 6,
                "description": "0=Monday, 6=Sunday.",
            },
            "kind": {
                "type": "string",
                "enum": ["strength", "social_run", "hard_run", "easy_run", "long_run", "mobility", "other"],
            },
            "label": {"type": "string", "description": "E.g. 'PT with trainer', 'Wednesday social run'."},
            "time_of_day": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "is_hard_constraint": {"type": "boolean"},
        },
        "required": ["day_of_week", "kind", "label"],
    },
}

REMOVE_TRAINING_ANCHOR_TOOL = {
    "name": "remove_training_anchor",
    "description": "Remove a training anchor that is no longer recurring.",
    "input_schema": {
        "type": "object",
        "properties": {
            "anchor_id": {"type": "string", "description": "UUID of the anchor to remove."},
        },
        "required": ["anchor_id"],
    },
}

RUN_PATTERN_SCAN_TOOL = {
    "name": "run_pattern_scan",
    "description": (
        "Analyse recent data and update the user's active insights. "
        "Call this when the user asks what patterns have been noticed, "
        "or asks for a data-driven review of her training or wellbeing."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

ADD_GOAL_TOOL = {
    "name": "add_goal",
    "description": (
        "Add a new goal or waypoint. Use for races, aerobic targets, strength milestones, "
        "or any other named goal. Races have fixed dates; other waypoints can move."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "goal_type": {"type": "string", "enum": ["race", "aerobic", "strength", "general"]},
            "target_date_iso": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "is_fixed_date": {"type": "boolean"},
            "metrics": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["title", "goal_type"],
    },
}

LIST_GOALS_TOOL = {
    "name": "list_goals",
    "description": "List all active goals and waypoints.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

SYNC_GARMIN_TOOL = {
    "name": "sync_garmin",
    "description": "Sync recent Garmin data. Use when data looks stale or user asks to sync.",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
        },
        "required": [],
    },
}

GET_GARMIN_ACTIVITIES_TOOL = {
    "name": "get_garmin_activities",
    "description": "List recent Garmin activities with optional filtering by type.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "activity_type": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Filter by activity type, e.g. 'running', 'cycling', 'strength'.",
            },
        },
        "required": [],
    },
}

GET_GARMIN_ACTIVITY_DETAIL_TOOL = {
    "name": "get_garmin_activity_detail",
    "description": "Get detailed view of one activity. Omit activity_id to get the most recent.",
    "input_schema": {
        "type": "object",
        "properties": {
            "activity_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Activity ID to look up. Omit for latest activity.",
            },
        },
        "required": [],
    },
}

PUSH_WORKOUT_TO_WATCH_TOOL = {
    "name": "push_workout_to_watch",
    "description": "Build and push a planned workout to Garmin Connect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "sport": {
                "type": "string",
                "enum": ["running", "cycling", "strength_training", "cardio"],
            },
            "description": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "duration_minutes": {"type": "number"},
                        "target_pace_per_km": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "target_hr_low": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "target_hr_high": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    },
                    "required": ["type", "duration_minutes"],
                },
            },
            "schedule_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "ISO date (YYYY-MM-DD) to schedule the workout on the watch.",
            },
        },
        "required": ["name", "sport", "steps"],
    },
}

LIST_GARMIN_WORKOUTS_TOOL = {
    "name": "list_garmin_workouts",
    "description": "List workouts saved in Garmin Connect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
        "required": [],
    },
}

DELETE_GARMIN_WORKOUT_TOOL = {
    "name": "delete_garmin_workout",
    "description": "Delete a workout from Garmin Connect by ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "workout_id": {"type": "string"},
        },
        "required": ["workout_id"],
    },
}

UPDATE_GOAL_TOOL = {
    "name": "update_goal",
    "description": (
        "Update an existing goal — move the target date, update metrics, "
        "mark as achieved, change the title, or remove a duplicate by setting status to 'dropped'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal_id": {"type": "string"},
            "title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "target_date_iso": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "is_fixed_date": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
            "metrics": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "status": {
                "anyOf": [
                    {"type": "string", "enum": ["active", "achieved", "paused", "dropped"]},
                    {"type": "null"},
                ],
            },
        },
        "required": ["goal_id"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_get_training_plan(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    return training_service.get_plan_text(user_id, now)


def handle_get_session_detail(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    return training_service.get_session_text(user_id, input_dict["session_kind"], now)


def handle_adjust_training(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    from trellis.training_service import TrainingAction, TrainingOperation

    action_map = {
        "explain_plan": TrainingAction.EXPLAIN_PLAN,
        "today_training": TrainingAction.TODAY_TRAINING,
    }
    raw_action = input_dict.get("action", "explain_plan")
    action = action_map.get(raw_action, TrainingAction.EXPLAIN_PLAN)

    operation = TrainingOperation(
        action=action,
        summary=raw_action.replace("_", " "),
        detail=False,
        session_kind=None,
        clarification_question=None,
    )
    return training_service.execute_training_operation(user_id, operation, now)


def handle_get_health_summary(
    user_id: UUID, input_dict: dict, now: datetime,
    *, health_status_service, health_repository, garmin_sync, timezone,
) -> str:
    if health_status_service is None:
        return "Garmin data is not available."
    if garmin_sync is not None:
        try:
            garmin_sync.sync_recent(user_id, days=3)
        except Exception:
            _log.warning("Garmin sync failed", exc_info=True)
    summary = health_status_service.telegram_summary(user_id)
    today = now.astimezone(timezone).date()
    try:
        reports = health_repository.list_self_reports(user_id, today)
        if reports:
            r = reports[-1]
            parts = []
            if r.energy_score is not None:
                parts.append(f"energy {r.energy_score}/10")
            if r.body_score is not None:
                parts.append(f"body {r.body_score}/10")
            if r.life_load_score is not None:
                parts.append(f"life load {r.life_load_score}/10")
            if r.soreness_score is not None:
                parts.append(f"soreness {r.soreness_score}/10")
            self_report_line = "Self-report today: " + ", ".join(parts) if parts else "Self-report today: logged (no scores)"
        else:
            self_report_line = "Self-report today: not yet logged"
    except Exception:
        self_report_line = "Self-report today: unavailable"
    return f"{self_report_line}\n\n{summary}"


def handle_record_morning_checkin(
    user_id: UUID, input_dict: dict, now: datetime,
    *, health_repository, timezone,
) -> str:
    from trellis.health import SelfHealthReport

    today = now.astimezone(timezone).date()
    report = SelfHealthReport(
        user_id=user_id,
        observed_on=today,
        energy_score=input_dict.get("energy"),
        body_score=input_dict.get("body"),
        life_load_score=input_dict.get("life_load"),
        soreness_score=input_dict.get("soreness"),
        sleep_minutes=None,
        note=input_dict.get("notes") or None,
        raw={"source": "telegram_morning_oracle", "tool_input": input_dict},
    )
    health_repository.record_self_report(report)
    parts = [
        f"energy {report.energy_score}/10",
        f"body {report.body_score}/10",
        f"life load {report.life_load_score}/10",
    ]
    if report.soreness_score is not None:
        parts.append(f"soreness {report.soreness_score}/10")
    if report.note:
        parts.append(f"notes: {report.note}")
    return "Morning check-in recorded: " + ", ".join(parts)


def handle_record_post_workout_checkin(
    user_id: UUID, input_dict: dict, now: datetime,
    *, workout_checkin_service, timezone,
) -> str:
    if workout_checkin_service is None:
        return "Workout check-in is not available."
    today = now.astimezone(timezone).date()
    session_date, err = _parse_date_iso(input_dict.get("session_date_iso"), today)
    if err:
        return err
    raw_effort = input_dict.get("perceived_effort")
    perceived_effort = max(1, min(10, int(raw_effort))) if raw_effort is not None else None
    checkin = workout_checkin_service.record(
        user_id,
        input_dict["session_kind"],
        session_date,
        perceived_effort=perceived_effort,
        feel_note=input_dict.get("feel_note") or None,
        soreness_note=input_dict.get("soreness_note") or None,
    )
    parts = [f"{checkin.session_kind.replace('_', ' ')} check-in saved"]
    if checkin.perceived_effort is not None:
        parts.append(f"RPE {checkin.perceived_effort}/10")
    if checkin.feel_note:
        parts.append(checkin.feel_note)
    if checkin.soreness_note:
        parts.append(f"soreness: {checkin.soreness_note}")
    return " — ".join(parts)


def handle_record_strength_session(
    user_id: UUID, input_dict: dict, now: datetime,
    *, strength_session_service, timezone,
) -> str:
    if strength_session_service is None:
        return "Strength session logging is not available."
    today = now.astimezone(timezone).date()
    session_date, err = _parse_date_iso(input_dict.get("session_date_iso"), today)
    if err:
        return err
    session = strength_session_service.record(
        user_id,
        session_date,
        input_dict.get("exercises") or [],
        program_phase=input_dict.get("program_phase") or None,
        notes=input_dict.get("notes") or None,
    )
    exercise_summary = ", ".join(e.display() for e in session.exercises) or "no exercises logged"
    phase = f" ({session.program_phase})" if session.program_phase else ""
    return f"Strength session saved{phase}: {exercise_summary}"


def handle_get_week_completion(
    user_id: UUID, input_dict: dict, now: datetime,
    *, completion_service, timezone,
) -> str:
    if completion_service is None:
        return "Session completion not available."
    today = now.astimezone(timezone).date()
    week_start = today - timedelta(days=today.weekday())
    return completion_service.format_week_completion(user_id, week_start, today)


def handle_get_week_review(
    user_id: UUID, input_dict: dict, now: datetime,
    *, completion_service, workout_checkin_service, strength_session_service, health_repository, timezone,
) -> str:
    today = now.astimezone(timezone).date()
    days_since_monday = today.weekday()
    this_week_start = today - timedelta(days=days_since_monday)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(days=1)

    sections: list[str] = [
        f"Week of {last_week_start.strftime('%-d %b')} – {last_week_end.strftime('%-d %b')}:"
    ]

    if completion_service is not None:
        try:
            completion = completion_service.format_week_completion(
                user_id, last_week_start, last_week_end
            )
            body = completion.partition("\n")[2].strip()
            if body:
                sections.append(body)
        except Exception:
            _log.warning("get_week_review: completion data unavailable", exc_info=True)
            sections.append("(session completion data unavailable)")

    if workout_checkin_service is not None:
        try:
            checkins = workout_checkin_service.list_recent(user_id, limit=28)
            week_checkins = [
                c for c in checkins
                if last_week_start <= c.checked_in_on <= last_week_end
            ]
            if week_checkins:
                lines = ["How sessions felt:"]
                for c in week_checkins:
                    parts = [f"  {c.checked_in_on.strftime('%a')} {(c.session_kind or '').replace('_', ' ')}"]
                    if c.perceived_effort is not None:
                        parts.append(f"RPE {c.perceived_effort}/10")
                    if c.feel_note:
                        parts.append(c.feel_note)
                    if c.soreness_note:
                        parts.append(f"soreness: {c.soreness_note}")
                    lines.append(" — ".join(parts))
                sections.append("\n".join(lines))
        except Exception:
            _log.warning("get_week_review: checkin data unavailable", exc_info=True)

    if strength_session_service is not None:
        try:
            sessions = strength_session_service.list_recent(user_id, limit=14)
            week_strength = [
                s for s in sessions
                if last_week_start <= s.session_date <= last_week_end
            ]
            if week_strength:
                lines = ["PT/strength:"]
                for s in week_strength:
                    exercises = ", ".join(e.display() for e in s.exercises)
                    line = f"  {s.session_date.strftime('%a')}"
                    if exercises:
                        line += f": {exercises}"
                    if s.notes:
                        line += f" — {s.notes}"
                    lines.append(line)
                sections.append("\n".join(lines))
        except Exception:
            _log.warning("get_week_review: strength data unavailable", exc_info=True)

    if health_repository is not None:
        try:
            body_lines = []
            for i in range(7):
                d = last_week_start + timedelta(days=i)
                reports = health_repository.list_self_reports(user_id, d)
                if reports:
                    r = reports[-1]
                    parts: list[str] = [d.strftime("%a")]
                    if r.energy_score is not None:
                        parts.append(f"energy {r.energy_score}")
                    if r.body_score is not None:
                        parts.append(f"body {r.body_score}")
                    if r.life_load_score is not None:
                        parts.append(f"load {r.life_load_score}")
                    if len(parts) > 1:
                        body_lines.append("  " + " / ".join(parts))
            if body_lines:
                sections.append("Body scores:\n" + "\n".join(body_lines))
        except Exception:
            _log.warning("get_week_review: body score data unavailable", exc_info=True)

    return "\n\n".join(sections)


def handle_get_training_arc(user_id: UUID, input_dict: dict, now: datetime, *, arc_repository) -> str:
    if arc_repository is None:
        return "Training arc is not available."
    arc = arc_repository.get_active(user_id)
    if arc is None:
        return "No training arc yet. Ask me to build one."
    return _format_arc_for_display(arc, now.date())


def handle_generate_training_arc(
    user_id: UUID, input_dict: dict, now: datetime,
    *, goal_service,
) -> str:
    if goal_service is None:
        return "No goals found — save a race goal first."
    goals = goal_service.list_active(user_id)
    if not goals:
        return "No active goals found — save a race goal first, then build the arc."
    goal_summary = "\n".join(g.summary() for g in goals)
    extra = input_dict.get("context", "")
    lines = [
        "Goals:",
        goal_summary,
        f"Today: {now.date().isoformat()}",
    ]
    if extra:
        lines.append(f"Additional context: {extra}")
    lines.append("Generate the arc phases and call save_training_arc.")
    return "\n".join(lines)


def handle_save_week_plan(
    user_id: UUID, input_dict: dict, now: datetime,
    *, training_repository, planner, anchor_service, timezone,
) -> str:
    from trellis.training import PlanMode, PlanningRequest, Weekday
    from trellis.training_service import TrainingService

    today = now.astimezone(timezone).date()
    week_start = _target_week_start(today)

    raw_mode = input_dict.get("mode", "BUILD")
    mode = PlanMode.BUILD if raw_mode == "BUILD" else PlanMode.DELOAD

    sessions = _parse_claude_sessions(input_dict.get("sessions", []))
    if not sessions and mode == PlanMode.BUILD:
        return "No valid sessions in plan — check session format (day, kind, blocks required)."

    strength_days: tuple = ()
    if anchor_service is not None:
        try:
            anchors = anchor_service.list(user_id)
            strength_days = tuple(
                Weekday(a.day_of_week) for a in anchors if a.kind == "strength"
            )
        except Exception:
            _log.warning("save_week_plan: anchor lookup failed", exc_info=True)

    try:
        request = PlanningRequest(
            week_start=week_start,
            mode=mode,
            strength_days=strength_days,
            claude_sessions=sessions,
        )
    except ValueError as exc:
        return f"Plan request error: {exc}"

    plan = planner.plan(request)
    rationale = input_dict.get("rationale", "")
    if rationale:
        from dataclasses import replace as _replace
        plan = _replace(plan, rationale=(rationale,) + plan.rationale)

    saved = training_repository.save_active(user_id, plan)
    return TrainingService.format_plan(saved, heading="Training plan saved")


def handle_save_training_arc(
    user_id: UUID, input_dict: dict, now: datetime,
    *, arc_repository,
) -> str:
    from datetime import timezone as _tz
    from trellis.training_arc import ArcPhase, TrainingArc

    phases_raw = input_dict.get("phases", [])
    if not phases_raw:
        return "No phases provided."
    try:
        phases = [ArcPhase.from_dict(p) for p in phases_raw]
    except (KeyError, ValueError) as exc:
        return f"Could not parse arc phases: {exc}"

    arc = TrainingArc(
        id=uuid4(),
        user_id=user_id,
        goal_id=None,
        phases=phases,
        notes=None,
        generated_at=now,
    )
    saved = arc_repository.save(arc)
    arc_repository.deactivate_others(user_id, saved.id)
    return _format_arc_for_display(saved, now.date())


def handle_apply_readiness_adaptation(
    user_id: UUID, input_dict: dict, now: datetime,
    *, training_repository, timezone,
) -> str:
    from trellis.training import SessionKind, Weekday
    from trellis.training_service import TrainingService

    today = now.astimezone(timezone).date()
    week_start = _week_start(today)
    plan = training_repository.latest_active(user_id, week_start)
    if plan is None:
        return "No active training plan for this week."

    session_raw = input_dict.get("session", {})
    session_raw["day"] = today.strftime("%A").lower()
    adapted = _parse_claude_session(session_raw)
    if adapted is None:
        return "Could not parse adapted session — check format."

    today_day = Weekday(today.weekday())
    _priority = {
        SessionKind.HARD_RUN: 0, SessionKind.SOCIAL_RUN: 1,
        SessionKind.LONG_RUN: 2, SessionKind.EASY_RUN: 3, SessionKind.MOBILITY: 4,
    }
    sessions = [
        s for s in plan.sessions
        if not (s.day == today_day and s.kind in _priority)
        or _priority.get(s.kind, 99) > _priority.get(adapted.kind, 99)
    ]
    sessions = [s for s in plan.sessions if s.day != today_day or s.kind == SessionKind.STRENGTH]
    sessions.append(replace(adapted, id=uuid4(), day=today_day))

    from dataclasses import replace as _replace
    from trellis.training import TrainingPlanner
    revised = _replace(
        plan,
        sessions=TrainingPlanner._sorted(sessions),
        rationale=plan.rationale + (f"Readiness adaptation applied {today.isoformat()}.",),
        revision=plan.revision + 1,
    )
    saved = training_repository.save_active(user_id, revised)
    return TrainingService.format_plan(saved, heading="Plan updated")


def handle_list_training_anchors(user_id: UUID, input_dict: dict, now: datetime, *, anchor_service) -> str:
    if anchor_service is None:
        return "Training anchors are not available."
    anchors = anchor_service.list(user_id)
    if not anchors:
        return "No training anchors set."
    lines = ["Training anchors:"]
    for a in anchors:
        lines.append(f"  [{a.id}] {a.describe()}")
    return "\n".join(lines)


def handle_set_training_anchor(user_id: UUID, input_dict: dict, now: datetime, *, anchor_service) -> str:
    if anchor_service is None:
        return "Training anchors are not available."
    anchor = anchor_service.set(
        user_id,
        day_of_week=int(input_dict["day_of_week"]),
        kind=input_dict["kind"],
        label=input_dict["label"],
        time_of_day=input_dict.get("time_of_day") or None,
        is_hard_constraint=input_dict.get("is_hard_constraint", True),
    )
    return f"Anchor saved: {anchor.describe()}"


def handle_remove_training_anchor(user_id: UUID, input_dict: dict, now: datetime, *, anchor_service) -> str:
    if anchor_service is None:
        return "Training anchors are not available."
    from uuid import UUID as _UUID
    anchor_service.remove(_UUID(input_dict["anchor_id"]))
    return f"Anchor {input_dict['anchor_id']} removed."


def handle_run_pattern_scan(user_id: UUID, input_dict: dict, now: datetime, *, pattern_engine) -> str:
    if pattern_engine is None:
        return "Pattern scanning is not available."
    today = now.date()
    insights = pattern_engine.run(user_id, today)
    if not insights:
        return "No new patterns detected — not enough data yet or nothing has changed."
    lines = [f"Pattern scan complete. {len(insights)} insight(s) updated:"]
    for ins in insights:
        lines.append(
            f"  [{ins.domain}] {ins.summary} (confidence: {ins.confidence:.0%}, n={ins.evidence_count})"
        )
    return "\n".join(lines)


def handle_add_goal(user_id: UUID, input_dict: dict, now: datetime, *, goal_service) -> str:
    target_date = None
    if input_dict.get("target_date_iso"):
        target_date = date.fromisoformat(input_dict["target_date_iso"])
    goal = goal_service.add(
        user_id,
        title=input_dict["title"],
        goal_type=input_dict["goal_type"],
        target_date=target_date,
        is_fixed_date=input_dict.get("is_fixed_date", False),
        metrics=input_dict.get("metrics") or {},
        notes=input_dict.get("notes"),
    )
    return f"Goal added: {goal.summary()}"


def handle_list_goals(user_id: UUID, input_dict: dict, now: datetime, *, goal_service) -> str:
    goals = goal_service.list_active(user_id)
    if not goals:
        return "No active goals."
    lines = ["Active goals:"]
    for g in goals:
        lines.append(f"  [{g.id}] {g.summary()}")
    return "\n".join(lines)


def handle_update_goal(user_id: UUID, input_dict: dict, now: datetime, *, goal_service) -> str:
    from uuid import UUID as _UUID
    goal_id = _UUID(input_dict["goal_id"])
    kwargs: dict[str, Any] = {}
    if input_dict.get("title") is not None:
        kwargs["title"] = input_dict["title"]
    if input_dict.get("target_date_iso") is not None:
        kwargs["target_date"] = date.fromisoformat(input_dict["target_date_iso"])
    if input_dict.get("is_fixed_date") is not None:
        kwargs["is_fixed_date"] = input_dict["is_fixed_date"]
    if input_dict.get("metrics") is not None:
        kwargs["metrics"] = input_dict["metrics"]
    if input_dict.get("notes") is not None:
        kwargs["notes"] = input_dict["notes"]
    if input_dict.get("status") is not None:
        kwargs["status"] = input_dict["status"]
    goal = goal_service.update(user_id, goal_id, **kwargs)
    return f"Goal updated: {goal.summary()}"


def handle_sync_garmin(
    user_id: UUID, input_dict: dict, now: datetime,
    *, garmin_sync,
) -> str:
    if garmin_sync is None:
        return "Garmin sync not configured."
    days = int(input_dict.get("days") or 7)
    days = max(1, min(30, days))
    try:
        summary = garmin_sync.sync_recent(user_id, days=days)
        return (
            f"Garmin sync complete: {summary.daily_health_records} daily health records, "
            f"{summary.activity_records} activities, {summary.activity_detail_records} activity details "
            f"({summary.start_date.isoformat()} to {summary.end_date.isoformat()})."
        )
    except Exception as exc:
        return f"Garmin sync failed: {exc}"


def handle_get_garmin_activities(
    user_id: UUID, input_dict: dict, now: datetime,
    *, health_repository,
) -> str:
    if health_repository is None:
        return "Health data not available."
    limit = int(input_dict.get("limit") or 10)
    limit = max(1, min(50, limit))
    activity_type = input_dict.get("activity_type") or None
    try:
        activities = health_repository.latest_activities(
            user_id, limit=limit, activity_type=activity_type
        )
        if not activities:
            return "No activities found."
        lines = []
        for a in activities:
            start = a.start_time_epoch_seconds
            ts = ""
            if start is not None:
                from datetime import timezone as _tz
                dt = datetime.fromtimestamp(start, tz=_tz.utc)
                ts = dt.strftime("%Y-%m-%d")
            dist = f"{a.distance_meters / 1000:.2f} km" if a.distance_meters else "—"
            dur = ""
            if a.duration_milliseconds:
                mins = int(a.duration_milliseconds / 1000 / 60)
                dur = f"{mins}min"
            hr = f"avg HR {a.average_heart_rate}" if a.average_heart_rate else ""
            parts = [p for p in [ts, a.name, dist, dur, hr] if p]
            lines.append("  " + " | ".join(parts))
        return f"Activities ({len(activities)}):\n" + "\n".join(lines)
    except Exception as exc:
        return f"Failed to load activities: {exc}"


def handle_get_garmin_activity_detail(
    user_id: UUID, input_dict: dict, now: datetime,
    *, health_repository,
) -> str:
    if health_repository is None:
        return "Health data not available."
    activity_id = input_dict.get("activity_id") or None
    try:
        activities = health_repository.latest_activities_with_detail(user_id, limit=50)
        if not activities:
            return "No activities found."
        if activity_id:
            match = next((a for a in activities if str(a.get("activity_id")) == activity_id), None)
            if match is None:
                return f"Activity {activity_id} not found."
        else:
            match = activities[0]
        lines = [f"Activity: {match.get('name', 'Unknown')}"]
        for key, value in match.items():
            if key == "name":
                continue
            if value is not None:
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to load activity detail: {exc}"


_SPORT_TYPE_MAP = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running"},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling"},
    "strength_training": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
    "cardio": {"sportTypeId": 26, "sportTypeKey": "cardio"},
}

_STEP_TYPE_MAP = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery"},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest"},
    "repeat": {"stepTypeId": 6, "stepTypeKey": "repeat"},
    "other": {"stepTypeId": 7, "stepTypeKey": "other"},
}


def _build_workout_json(name: str, sport: str, steps: list[dict]) -> str:
    import json as _json
    sport_type = _SPORT_TYPE_MAP.get(sport, _SPORT_TYPE_MAP["running"])
    workout_steps = []
    for i, step in enumerate(steps, start=1):
        raw_type = (step.get("type") or "other").lower()
        step_type = _STEP_TYPE_MAP.get(raw_type, _STEP_TYPE_MAP["other"])
        duration_seconds = int(float(step.get("duration_minutes", 0)) * 60)
        target_type = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
        hr_low = step.get("target_hr_low")
        hr_high = step.get("target_hr_high")
        if hr_low is not None and hr_high is not None:
            target_type = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
        workout_steps.append({
            "type": "ExecutableStepDTO",
            "stepOrder": i,
            "stepType": step_type,
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": duration_seconds,
            "targetType": target_type,
            **({"targetValueOne": hr_low, "targetValueTwo": hr_high} if hr_low and hr_high else {}),
        })
    workout = {
        "workoutName": name,
        "sportType": sport_type,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport_type,
            "workoutSteps": workout_steps,
        }],
    }
    return _json.dumps(workout)


def handle_push_workout_to_watch(
    user_id: UUID, input_dict: dict, now: datetime,
    *, garmin_direct,
) -> str:
    if garmin_direct is None:
        return "Garmin Connect integration not available."
    name = input_dict["name"]
    sport = input_dict.get("sport", "running")
    steps = input_dict.get("steps") or []
    schedule_date_str = input_dict.get("schedule_date") or None
    try:
        workout_json = _build_workout_json(name, sport, steps)
        workout_id = garmin_direct.push_workout(user_id, workout_json)
        result = f"Workout '{name}' pushed to Garmin Connect (ID: {workout_id})."
        if schedule_date_str:
            on_date = date.fromisoformat(schedule_date_str)
            garmin_direct.schedule_workout(user_id, workout_id, on_date)
            result += f" Scheduled for {on_date.isoformat()}."
        return result
    except Exception as exc:
        return f"Failed to push workout: {exc}"


def handle_list_garmin_workouts(
    user_id: UUID, input_dict: dict, now: datetime,
    *, garmin_direct,
) -> str:
    if garmin_direct is None:
        return "Garmin Connect integration not available."
    limit = int(input_dict.get("limit") or 20)
    limit = max(1, min(50, limit))
    try:
        workouts = garmin_direct.list_workouts(user_id, limit=limit)
        if not workouts:
            return "No workouts found in Garmin Connect."
        lines = [f"Garmin workouts ({len(workouts)}):"]
        for w in workouts:
            wid = w.get("workoutId", "?")
            wname = w.get("workoutName", "Unnamed")
            sport = (w.get("sportType") or {}).get("sportTypeKey", "")
            parts = [p for p in [wname, sport, f"ID: {wid}"] if p]
            lines.append("  " + " | ".join(parts))
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to list workouts: {exc}"


def handle_delete_garmin_workout(
    user_id: UUID, input_dict: dict, now: datetime,
    *, garmin_direct,
) -> str:
    if garmin_direct is None:
        return "Garmin Connect integration not available."
    workout_id = input_dict["workout_id"]
    try:
        garmin_direct.delete_workout(user_id, workout_id)
        return f"Workout {workout_id} deleted from Garmin Connect."
    except Exception as exc:
        return f"Failed to delete workout {workout_id}: {exc}"


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

TRAINING_SIGNALS = [
    "run", "running", "training", "plan", "session", "workout",
    "strength", "PT", "gym", "pace", "hard", "easy", "long", "social run",
    "mobility", "stretching", "garmin", "readiness", "body battery", "HRV",
    "heart rate", "sleep score", "week review", "week completion", "this week",
    "last week", "morning", "check-in", "energy", "body", "soreness", "RPE",
    "recovery", "race", "goal", "arc", "deload", "holiday week", "anchor",
    "interval", "splits", "km", "distance", "minutes",
    "sync", "push workout", "watch", "garmin connect", "activity", "activities",
    "schedule workout", "warmup", "cooldown", "zone",
]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def training_tools(
    training_service,
    health_repository,
    garmin_sync,
    timezone,
    health_status_service=None,
    goal_service=None,
    completion_service=None,
    workout_checkin_service=None,
    strength_session_service=None,
    pattern_engine=None,
    anchor_service=None,
    garmin_direct=None,
    arc_repository=None,
    planner=None,
    training_repository=None,
) -> list[tuple[dict, callable]]:
    return [
        (GET_TRAINING_PLAN_TOOL,
         lambda uid, inp, now: handle_get_training_plan(uid, inp, now, training_service=training_service)),
        (GET_SESSION_DETAIL_TOOL,
         lambda uid, inp, now: handle_get_session_detail(uid, inp, now, training_service=training_service)),
        (ADJUST_TRAINING_TOOL,
         lambda uid, inp, now: handle_adjust_training(uid, inp, now, training_service=training_service)),
        (GET_HEALTH_SUMMARY_TOOL,
         lambda uid, inp, now: handle_get_health_summary(
             uid, inp, now,
             health_status_service=health_status_service,
             health_repository=health_repository,
             garmin_sync=garmin_sync,
             timezone=timezone,
         )),
        (RECORD_MORNING_CHECKIN_TOOL,
         lambda uid, inp, now: handle_record_morning_checkin(
             uid, inp, now, health_repository=health_repository, timezone=timezone
         )),
        (RECORD_POST_WORKOUT_CHECKIN_TOOL,
         lambda uid, inp, now: handle_record_post_workout_checkin(
             uid, inp, now, workout_checkin_service=workout_checkin_service, timezone=timezone
         )),
        (RECORD_STRENGTH_SESSION_TOOL,
         lambda uid, inp, now: handle_record_strength_session(
             uid, inp, now, strength_session_service=strength_session_service, timezone=timezone
         )),
        (GET_WEEK_COMPLETION_TOOL,
         lambda uid, inp, now: handle_get_week_completion(
             uid, inp, now, completion_service=completion_service, timezone=timezone
         )),
        (GET_WEEK_REVIEW_TOOL,
         lambda uid, inp, now: handle_get_week_review(
             uid, inp, now,
             completion_service=completion_service,
             workout_checkin_service=workout_checkin_service,
             strength_session_service=strength_session_service,
             health_repository=health_repository,
             timezone=timezone,
         )),
        (GET_TRAINING_ARC_TOOL,
         lambda uid, inp, now: handle_get_training_arc(uid, inp, now, arc_repository=arc_repository)),
        (GENERATE_TRAINING_ARC_TOOL,
         lambda uid, inp, now: handle_generate_training_arc(uid, inp, now, goal_service=goal_service)),
        (SAVE_WEEK_PLAN_TOOL,
         lambda uid, inp, now: handle_save_week_plan(
             uid, inp, now,
             training_repository=training_repository,
             planner=planner,
             anchor_service=anchor_service,
             timezone=timezone,
         )),
        (SAVE_TRAINING_ARC_TOOL,
         lambda uid, inp, now: handle_save_training_arc(uid, inp, now, arc_repository=arc_repository)),
        (APPLY_READINESS_ADAPTATION_TOOL,
         lambda uid, inp, now: handle_apply_readiness_adaptation(
             uid, inp, now, training_repository=training_repository, timezone=timezone
         )),
        (LIST_TRAINING_ANCHORS_TOOL,
         lambda uid, inp, now: handle_list_training_anchors(uid, inp, now, anchor_service=anchor_service)),
        (SET_TRAINING_ANCHOR_TOOL,
         lambda uid, inp, now: handle_set_training_anchor(uid, inp, now, anchor_service=anchor_service)),
        (REMOVE_TRAINING_ANCHOR_TOOL,
         lambda uid, inp, now: handle_remove_training_anchor(uid, inp, now, anchor_service=anchor_service)),
        (RUN_PATTERN_SCAN_TOOL,
         lambda uid, inp, now: handle_run_pattern_scan(uid, inp, now, pattern_engine=pattern_engine)),
        (ADD_GOAL_TOOL,
         lambda uid, inp, now: handle_add_goal(uid, inp, now, goal_service=goal_service)),
        (LIST_GOALS_TOOL,
         lambda uid, inp, now: handle_list_goals(uid, inp, now, goal_service=goal_service)),
        (UPDATE_GOAL_TOOL,
         lambda uid, inp, now: handle_update_goal(uid, inp, now, goal_service=goal_service)),
        (SYNC_GARMIN_TOOL,
         lambda uid, inp, now: handle_sync_garmin(uid, inp, now, garmin_sync=garmin_sync)),
        (GET_GARMIN_ACTIVITIES_TOOL,
         lambda uid, inp, now: handle_get_garmin_activities(
             uid, inp, now, health_repository=health_repository
         )),
        (GET_GARMIN_ACTIVITY_DETAIL_TOOL,
         lambda uid, inp, now: handle_get_garmin_activity_detail(
             uid, inp, now, health_repository=health_repository
         )),
        (PUSH_WORKOUT_TO_WATCH_TOOL,
         lambda uid, inp, now: handle_push_workout_to_watch(
             uid, inp, now, garmin_direct=garmin_direct
         )),
        (LIST_GARMIN_WORKOUTS_TOOL,
         lambda uid, inp, now: handle_list_garmin_workouts(
             uid, inp, now, garmin_direct=garmin_direct
         )),
        (DELETE_GARMIN_WORKOUT_TOOL,
         lambda uid, inp, now: handle_delete_garmin_workout(
             uid, inp, now, garmin_direct=garmin_direct
         )),
    ]
