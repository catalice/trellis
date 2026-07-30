"""
Tools for the running coach — the coach's hands. The coaching itself happens in the
oracle turn (persona in domain_training_claude); these just let it read context and
persist the plan.

Handler signature: (user_id, input_dict, now) -> str
Context loader: training_context_loader (Tier 1b — carries the coach persona)
Snapshot: training_snapshot (Tier 2 — today's run, existence only)
Registration: training_tools(...)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_training_claude import TRAINING_COACH_GUIDANCE

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TRAINING_GET_TOOL: dict = {
    "name": "training_get",
    "description": "Read the running plan before telling the user what's on. Use this so you speak from what's actually stored, not memory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["plan", "week", "today", "baseline", "history"],
                "description": (
                    "plan: the arc + the stored week. "
                    "week: this week's REAL dates (weekday->date) plus any stored sessions. "
                    "today: today's stored session. "
                    "baseline: the stored fitness baseline. "
                    "history: recent completed runs — read before reviewing a week or planning the next."
                ),
            }
        },
        "required": ["what"],
    },
}

SAVE_TRAINING_PLAN_TOOL: dict = {
    "name": "save_training_plan",
    "description": (
        "Persist the plan you've designed or adjusted. Author it from the REAL dates you "
        "were given (never invent dates). Call whenever you create or change the arc or the "
        "week, so it survives to next time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "description": (
                    'The plan doc: {"arc": "<phases, weeks to goal, where they are now>", '
                    '"week": [{"date": "YYYY-MM-DD", "type": "easy|long|intervals|tempo|recovery|rest", '
                    '"detail": "what to do"}, ...]} using this week\'s real dates.'
                ),
            },
            "baseline": {
                "type": "string",
                "description": "Optional: a short fitness baseline summary to store/update.",
            },
        },
        "required": ["plan"],
    },
}

LOG_RUN_TOOL: dict = {
    "name": "log_run",
    "description": (
        "Record a run the user completed, so it shapes what you plan next. Use when they "
        "tell you they ran ('finished my 5k, felt strong'). Defaults to today unless they "
        "say otherwise."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "What they did, in plain words — e.g. 'easy 5k, felt strong, HR stayed low'.",
            },
            "date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD) if it wasn't today.",
            },
            "distance_km": {
                "type": "number",
                "description": "Optional distance in km if known.",
            },
        },
        "required": ["note"],
    },
}

PROVIDE_TRAINING_DATA_TOOL: dict = {
    "name": "provide_training_data",
    "description": (
        "Parse a Garmin activity-export CSV the user pasted/provided into a running baseline "
        "(weekly volume, paces, HR, longest run). Read the returned numbers, then summarise "
        "them back to the user and save with save_training_plan(baseline=...)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "csv": {"type": "string", "description": "The raw CSV text of the Garmin activity export."},
        },
        "required": ["csv"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_training_get(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    what = str(input_dict.get("what", ""))

    if what == "today":
        session = training_service.todays_session(user_id, now)
        if session is None:
            return "Nothing stored for today. If there's no plan yet, offer to build one."
        return "Today: " + _fmt_session(session)

    if what == "week":
        real = training_service.current_week(now)
        stored = {s.get("date"): s for s in training_service.week_sessions(user_id)}
        lines = ["This week (real dates):"]
        for day in real:
            marker = " <- today" if day["is_today"] else ""
            s = stored.get(day["date"])
            planned = f"  {_fmt_session(s)}" if s else "  (nothing planned yet)"
            lines.append(f"{day['weekday']} {day['date']}{marker}:{planned[1:] if s else planned}")
        return "\n".join(lines)

    if what == "plan":
        plan = training_service.get_plan(user_id)
        if plan is None or not plan.plan:
            return "No plan stored yet. Understand their goal + starting point, then build one."
        arc = plan.plan.get("arc") or "(no arc summary yet)"
        lines = [f"Arc: {arc}"]
        sessions = training_service.week_sessions(user_id)
        if sessions:
            lines.append("Stored week:")
            lines.extend("  " + _fmt_session(s) for s in sessions)
        return "\n".join(lines)

    if what == "baseline":
        plan = training_service.get_plan(user_id)
        if plan is None or not plan.baseline:
            return "No baseline yet. Ask for their Garmin data or what they can currently run."
        return f"Baseline: {plan.baseline}"

    if what == "history":
        runs = training_service.recent_runs(user_id)
        if not runs:
            return "No runs logged yet. When they tell you they ran, record it with log_run."
        lines = ["Recent runs (most recent first):"]
        for r in runs:
            dist = f" — {r.distance_km}km" if r.distance_km is not None else ""
            lines.append(f"  {r.ran_on.isoformat()}{dist}: {r.note}")
        return "\n".join(lines)

    return "Unknown request. Use what: plan, week, today, baseline, or history."


def handle_save_training_plan(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    plan = input_dict.get("plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            return "The plan needs to be a JSON object with 'arc' and 'week'."
    if not isinstance(plan, dict):
        return "The plan needs to be a JSON object with 'arc' and 'week'."
    baseline = input_dict.get("baseline")
    baseline = str(baseline) if baseline is not None else None
    try:
        goals = training_service.training_goals(user_id)
        goal_id = goals[0].id if goals else None
        training_service.save_plan(user_id, plan=plan, baseline=baseline, goal_id=goal_id)
    except Exception:
        _log.warning("save_training_plan failed", exc_info=True)
        return "Couldn't save the plan just now — try again in a moment."
    n = len([s for s in plan.get("week", []) if isinstance(s, dict)])
    return f"Saved the plan ({n} day(s) this week)."


def handle_provide_training_data(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    csv_text = str(input_dict.get("csv", "")).strip()
    if not csv_text:
        return "No CSV content received."
    summary = training_service.parse_garmin_csv(csv_text)
    return "Parsed baseline (interpret and summarise for the user, then save it):\n" + json.dumps(summary, indent=2)


def handle_log_run(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    note = str(input_dict.get("note", "")).strip()
    if not note:
        return "Nothing to log — what run did they do?"
    ran_on = None
    raw_date = str(input_dict.get("date", "")).strip()
    if raw_date:
        try:
            ran_on = date.fromisoformat(raw_date)
        except ValueError:
            ran_on = None  # fall back to today
    distance = input_dict.get("distance_km")
    try:
        distance = float(distance) if distance is not None else None
    except (TypeError, ValueError):
        distance = None
    try:
        run = training_service.log_run(user_id, note, now=now, ran_on=ran_on, distance_km=distance)
    except Exception:
        _log.warning("log_run failed", exc_info=True)
        return "Couldn't log that run just now — try again in a moment."
    dist = f" ({run.distance_km}km)" if run.distance_km is not None else ""
    return f"Logged: {run.ran_on.isoformat()}{dist} — {run.note}"


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + snapshot (Tier 2)
# ---------------------------------------------------------------------------

def training_context_loader(training_service, goal_reader) -> ContextLoader:
    """Loaded only when training is routed. Carries the coach persona + the goal +
    the stored plan + THIS WEEK's real dates, so the coach speaks from reality and
    never invents dates."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [TRAINING_COACH_GUIDANCE]

        try:
            goals = goal_reader.list_training_goals(user_id)
            if goals:
                parts.append("Training goal(s):\n" + "\n".join(f"  {g.summary()}" for g in goals))
            else:
                parts.append("No training goal set yet — help them define a realistic one.")
        except Exception:
            _log.warning("training_context: goals load failed", exc_info=True)

        try:
            plan = training_service.get_plan(user_id)
            if plan is None or not plan.plan:
                parts.append("No plan stored yet — understand their starting point, then build one.")
            else:
                if plan.plan.get("arc"):
                    parts.append("Arc: " + str(plan.plan["arc"]))
                if plan.baseline:
                    parts.append("Baseline: " + plan.baseline)
        except Exception:
            _log.warning("training_context: plan load failed", exc_info=True)

        # Always give the real calendar so runs land on real days.
        try:
            week = training_service.current_week(now)
            parts.append(
                "This week's real dates:\n"
                + "\n".join(f"  {d['weekday']} {d['date']}" + (" (today)" if d["is_today"] else "") for d in week)
            )
        except Exception:
            _log.warning("training_context: week dates failed", exc_info=True)

        return "[Training]\n" + "\n\n".join(parts)

    return loader


def training_snapshot(training_service) -> ContextLoader:
    """Tier 2 — today's run, existence only, always loaded."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        try:
            session = training_service.todays_session(user_id, now)
        except Exception:
            _log.warning("training_snapshot failed", exc_info=True)
            return None
        if not session:
            return None
        return f"Today's run: {session.get('type', 'run')} — {str(session.get('detail', ''))[:50]}"

    return loader


# ---------------------------------------------------------------------------
# Routing signals
# ---------------------------------------------------------------------------

TRAINING_SIGNALS: list[str] = [
    "run", "running", "ran", "jog", "training", "train",
    "workout", "session", "long run", "easy run", "intervals", "tempo",
    "pace", "mileage", "marathon", "5k", "10k", "half marathon", "race", "coach",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def training_tools(training_service) -> list[tuple[dict, Any]]:
    return [
        (TRAINING_GET_TOOL,
         lambda uid, inp, now: handle_training_get(uid, inp, now, training_service=training_service)),
        (SAVE_TRAINING_PLAN_TOOL,
         lambda uid, inp, now: handle_save_training_plan(uid, inp, now, training_service=training_service)),
        (LOG_RUN_TOOL,
         lambda uid, inp, now: handle_log_run(uid, inp, now, training_service=training_service)),
        (PROVIDE_TRAINING_DATA_TOOL,
         lambda uid, inp, now: handle_provide_training_data(uid, inp, now, training_service=training_service)),
    ]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_session(s: dict) -> str:
    stype = s.get("type", "run")
    detail = s.get("detail", "")
    day = s.get("date", "")
    line = f"{day} — {stype}"
    if detail:
        line += f": {detail}"
    return line
