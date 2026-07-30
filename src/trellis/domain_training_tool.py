"""
Tool schemas + handlers for the training (running) domain.

Handler signature: (user_id, input_dict, now) -> str
Context loader: training_context_loader (Tier 1b, carries the coach persona)
Snapshot: training_snapshot (Tier 2 — today's run, existence only)
Registration: training_tools(...)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_training_models import PlannedSession, SessionStatus
from trellis.domain_training_service import TrainingNotConnectedError

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Coach persona — DRAFT (rides the context loader, like the music companion).
# Loaded only when training is the routed domain. Voice to be shaped with the user.
# ---------------------------------------------------------------------------

TRAINING_COACH_PERSONA = """\
You are the user's running coach. Your job is to carry the plan so they don't have to \
think — they should never have to decide "what run is today", because you already \
know, and it's building toward their goal. (Use their name and anything you know \
about them from their profile; never assume a gender.)

How to coach:
- Hold the structure. There's a plan and a goal; keep them pointed at it. When they \
ask what's on, tell them plainly and with a bit of belief in them.
- Guide, don't limit — but don't coddle either. Adapt around real life, yet know the \
difference between "genuinely needs to back off" and "just doesn't fancy it", and \
hold the line on the second. A missed easy run isn't a crisis; drifting off the goal is.
- Be honest and a little demanding. They asked for this to actually WORK — so make it \
work. Encourage effort, don't reflexively hand out rest days.
- Talk about their actual plan and today's session, not running in the abstract.
- Warm, direct, in their corner. Celebrate the runs done; nudge the ones dodged.\
"""


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TRAINING_GET_TOOL: dict = {
    "name": "training_get",
    "description": "Retrieve the running plan. Use before telling the user what to run or what's coming up.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["today", "week", "plan"],
                "description": (
                    "today: today's scheduled session. "
                    "week: this week's sessions (Mon-Sun). "
                    "plan: the active plan's shape + the next couple of weeks."
                ),
            }
        },
        "required": ["what"],
    },
}

BUILD_TRAINING_PLAN_TOOL: dict = {
    "name": "build_training_plan",
    "description": (
        "Generate a fresh running plan from the user's training goal(s). Use when they have "
        "no plan yet, want to start over, or their goal has changed. Reads the goal "
        "from the second brain — they must have set a training goal first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days_per_week": {
                "type": "integer",
                "description": "How many days a week the user wants to run (1-7). Default 4 if unsure.",
            }
        },
    },
}

MARK_SESSION_TOOL: dict = {
    "name": "mark_session",
    "description": "Mark a training session done or skipped. Get its id from training_get first.",
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session's id."},
            "status": {"type": "string", "enum": ["done", "skipped"]},
        },
        "required": ["session_id", "status"],
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
            plan = training_service.active_plan(user_id)
            if plan is None:
                return "No training plan yet. Set a running goal, then I'll build one."
            return "Nothing scheduled for today in the current plan."
        return "Today: " + _fmt_session(session)

    if what == "week":
        sessions = training_service.this_week(user_id, now)
        if not sessions:
            plan = training_service.active_plan(user_id)
            if plan is None:
                return "No training plan yet. Set a running goal, then I'll build one."
            return "No sessions scheduled this week."
        lines = ["This week:"]
        lines.extend("  " + _fmt_session(s) for s in sessions)
        return "\n".join(lines)

    if what == "plan":
        plan = training_service.active_plan(user_id)
        if plan is None:
            return "No active training plan. Set a running goal and I'll build one."
        lines = []
        if plan.rationale:
            lines.append(f"Plan: {plan.rationale}")
        upcoming = training_service.upcoming(user_id, now)
        if upcoming:
            lines.append("Coming up:")
            lines.extend("  " + _fmt_session(s) for s in upcoming)
        if training_service.is_plan_stale(user_id):
            lines.append("(Heads up: the goal has changed since this plan was built — worth rebuilding.)")
        return "\n".join(lines) if lines else "Plan is active but has no sessions."

    return "Unknown request. Use what: today, week, or plan."


def handle_build_training_plan(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    days = input_dict.get("days_per_week")
    try:
        days_per_week = int(days) if days is not None else 4
    except (TypeError, ValueError):
        days_per_week = 4

    try:
        plan = training_service.build_plan_from_goal(user_id, now=now, days_per_week=days_per_week)
    except TrainingNotConnectedError:
        return (
            "No running goal set yet — add one first (a race, a distance, or just "
            "'run 3x a week'), then I'll build the plan around it."
        )
    if plan is None:
        return "I couldn't put a plan together just now. Try again in a moment."

    lines = []
    if plan.rationale:
        lines.append(f"Built your plan: {plan.rationale}")
    week = training_service.this_week(user_id, now)
    if week:
        lines.append("This week:")
        lines.extend("  " + _fmt_session(s) for s in week)
    return "\n".join(lines) if lines else "Plan built."


def handle_mark_session(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    raw_id = str(input_dict.get("session_id", "")).strip()
    try:
        session_id = UUID(raw_id)
    except ValueError:
        return "That doesn't look like a valid session id."
    try:
        status = SessionStatus(str(input_dict.get("status", "")).strip())
    except ValueError:
        return "Status must be 'done' or 'skipped'."
    try:
        session = training_service.mark_session(user_id, session_id, status)
    except LookupError:
        return "Couldn't find that session."
    verb = "done" if status == SessionStatus.DONE else "skipped"
    return f"Marked {verb}: {_fmt_session(session)}"


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + snapshot (Tier 2)
# ---------------------------------------------------------------------------

def training_context_loader(training_service, goal_reader) -> ContextLoader:
    """Loaded only when training is routed. Carries the coach persona plus the
    active goal and this week's shape, so the coach speaks from the real plan."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [TRAINING_COACH_PERSONA]

        try:
            goals = goal_reader.list_training_goals(user_id)
            if goals:
                parts.append("Training goal(s):\n" + "\n".join(f"  {g.summary()}" for g in goals))
        except Exception:
            _log.warning("training_context: goals load failed", exc_info=True)

        try:
            plan = training_service.active_plan(user_id)
            if plan is None:
                parts.append("No active plan yet — offer to build one from their goal.")
            else:
                week = training_service.this_week(user_id, now)
                if week:
                    parts.append("This week:\n" + "\n".join(f"  {_fmt_session(s)}" for s in week))
                if training_service.is_plan_stale(user_id):
                    parts.append("NOTE: goal changed since the plan was built — suggest rebuilding.")
        except Exception:
            _log.warning("training_context: plan load failed", exc_info=True)

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
        if session is None or session.status != SessionStatus.PLANNED:
            return None
        return f"Today's run: {session.session_type.value} — {session.description[:50]}"

    return loader


# ---------------------------------------------------------------------------
# Routing signals
# ---------------------------------------------------------------------------

TRAINING_SIGNALS: list[str] = [
    "run", "running", "ran", "jog", "training", "train",
    "workout", "session", "long run", "easy run", "intervals", "tempo",
    "pace", "mileage", "marathon", "5k", "10k", "half marathon", "race",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def training_tools(training_service) -> list[tuple[dict, Any]]:
    return [
        (
            TRAINING_GET_TOOL,
            lambda uid, inp, now: handle_training_get(uid, inp, now, training_service=training_service),
        ),
        (
            BUILD_TRAINING_PLAN_TOOL,
            lambda uid, inp, now: handle_build_training_plan(uid, inp, now, training_service=training_service),
        ),
        (
            MARK_SESSION_TOOL,
            lambda uid, inp, now: handle_mark_session(uid, inp, now, training_service=training_service),
        ),
    ]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_session(s: PlannedSession) -> str:
    day = s.scheduled_date.strftime("%a %d %b")
    bits = [f"{day} — {s.session_type.value}"]
    if s.description:
        bits.append(s.description)
    extras = []
    if s.planned_distance_km is not None:
        extras.append(f"{s.planned_distance_km:g}km")
    if s.planned_duration_min is not None:
        extras.append(f"{s.planned_duration_min}min")
    line = ": ".join(bits)
    if extras:
        line += f" ({', '.join(extras)})"
    if s.status != SessionStatus.PLANNED:
        line += f" [{s.status.value}]"
    return f"[{s.id}] {line}"
