"""
Tool schemas and handlers for the second brain domain.

Handler signature: (user_id, input_dict, now) -> str
Context loaders: second_brain_context_loader, second_brain_snapshot
Registration: second_brain_tools(...)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_second_brain_models import (
    GoalStatus,
    GoalType,
    TaskEnergy,
    TaskPriority,
)
from trellis.domain_second_brain_service import GoalNotFoundError, TaskNotFoundError

_log = logging.getLogger(__name__)

# (user_id, now) -> context string or None
ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas — Claude tools API format
# ---------------------------------------------------------------------------

BRAIN_DUMP_TOOL: dict = {
    "name": "brain_dump",
    "description": (
        "Capture anything the user wants to offload from their working memory — "
        "ideas, tasks, questions, things she wants to remember, half-formed thoughts. "
        "Always available, regardless of what else is happening. "
        "Raw text is preserved exactly. Claude will synthesise, surface tasks, "
        "and return the cleaned version."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The raw dump — exactly what the user said or wrote. Don't clean it up before sending.",
            }
        },
        "required": ["text"],
    },
}

SECOND_BRAIN_GET_TOOL: dict = {
    "name": "second_brain_get",
    "description": "Retrieve second brain data. Use this before presenting tasks, goals, inbox captures, efforts, or reminders.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["tasks", "seeds", "goals", "inbox", "efforts", "reminders", "tracking"],
                "description": (
                    "tasks: open todos ordered by urgency, plus parked. "
                    "seeds: the exploration menu — for 'what could I explore'. "
                    "goals: all active goals. "
                    "inbox: unassigned captures for cleanup. "
                    "efforts: all efforts by intensity. "
                    "reminders: upcoming reminders in the next 24h. "
                    "tracking: recent state logs and meds/sleep/period events with IDs "
                    "(needed before delete_log_entry)."
                ),
            }
        },
        "required": ["what"],
    },
}

CREATE_TASK_TOOL: dict = {
    "name": "create_task",
    "description": (
        "Create a task or seed directly (when not part of a brain dump). "
        "kind='todo' for admin the user owes; kind='seed' for curiosity they might "
        "feed — explorations with zero obligation, never urgent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {
                "type": "string", "enum": ["todo", "seed"], "default": "todo",
                "description": "todo = obligation. seed = exploration, no due date, never nags.",
            },
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
            "energy": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Mental/physical energy needed. low=routine, high=deep focus.",
                "default": "medium",
            },
            "description": {"type": "string"},
            "due": {
                "type": "string",
                "description": (
                    "Due date/time in the USER'S LOCAL time: YYYY-MM-DDTHH:MM, or "
                    "YYYY-MM-DD if no time. Resolve relative phrases yourself using "
                    "today's date from context. No timezone conversion. Omit if no deadline."
                ),
            },
        },
        "required": ["title"],
    },
}

COMPLETE_TASK_TOOL: dict = {
    "name": "complete_task",
    "description": "Mark a task as done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "UUID of the task."}
        },
        "required": ["task_id"],
    },
}

UPDATE_TASK_TOOL: dict = {
    "name": "update_task",
    "description": (
        "Update a task or seed: title, priority, energy, kind, due date, "
        "description, or status. Delete/remove → status='dropped' (gone from "
        "every view, never again). Shelve for later → status='parked' (visible "
        "in its own section). Reclassify todo↔seed with kind. "
        "Only send fields that change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "energy": {"type": "string", "enum": ["low", "medium", "high"]},
            "kind": {"type": "string", "enum": ["todo", "seed"]},
            "status": {
                "type": "string", "enum": ["open", "dropped", "parked"],
                "description": "dropped = never again, invisible. parked = not now, shelved but visible. open = back on the list.",
            },
            "due": {
                "type": "string",
                "description": "User-local YYYY-MM-DDTHH:MM or YYYY-MM-DD. No timezone conversion.",
            },
            "description": {"type": "string"},
        },
        "required": ["task_id"],
    },
}

SET_REMINDER_TOOL: dict = {
    "name": "set_reminder",
    "description": "Set a reminder. Works for appointments, time-sensitive tasks, recurring nudges.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "What the reminder is for."},
            "remind_at": {
                "type": "string",
                "description": (
                    "Date and time in the USER'S LOCAL time, format YYYY-MM-DDTHH:MM. "
                    "Send exactly the time the user said — do NOT convert timezones; "
                    "Python handles that."
                ),
            },
            "task_id": {
                "type": "string",
                "description": "Optionally link to an existing task.",
            },
            "recur_daily": {
                "type": "boolean",
                "description": "If true, fires at the same time every day until cancelled.",
                "default": False,
            },
        },
        "required": ["label", "remind_at"],
    },
}

CANCEL_REMINDER_TOOL: dict = {
    "name": "cancel_reminder",
    "description": "Cancel a scheduled reminder.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reminder_id": {"type": "string"}
        },
        "required": ["reminder_id"],
    },
}

ADD_GOAL_TOOL: dict = {
    "name": "add_goal",
    "description": "Add a new goal. All goal types live here — training goals are a subset.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "goal_type": {
                "type": "string",
                "enum": ["race", "aerobic", "strength", "life", "habit", "general"],
                "description": (
                    "race/aerobic/strength = training goals (fed into training module). "
                    "life/habit/general = everything else."
                ),
            },
            "target_date": {
                "type": "string",
                "description": "ISO date string YYYY-MM-DD. Omit if open-ended.",
            },
            "is_fixed_date": {
                "type": "boolean",
                "description": "True if date cannot move (race day). False = aspirational.",
                "default": False,
            },
            "notes": {"type": "string"},
        },
        "required": ["title", "goal_type"],
    },
}

UPDATE_GOAL_TOOL: dict = {
    "name": "update_goal",
    "description": "Update a goal's details or status. Use status='achieved' when done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "goal_id": {"type": "string"},
            "title": {"type": "string"},
            "target_date": {"type": "string"},
            "is_fixed_date": {"type": "boolean"},
            "notes": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["active", "achieved", "paused", "dropped"],
            },
        },
        "required": ["goal_id"],
    },
}

LOG_STATE_TOOL: dict = {
    "name": "log_state",
    "description": (
        "Log how the user is doing right now — energy, mood, and body/context events. "
        "Call whenever she describes her state (answering a check-in or spontaneously), "
        "or mentions taking meds, sleep, or her period. Multiple logs per day are "
        "expected; the within-day curve is the point. Derive scores from her words; "
        "never ask her to rate herself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": (
                    "Her words about how she's doing, first person, verbatim — "
                    "never paraphrase into third person ('feeling flat', not "
                    "'she feels flat'). Voice notes: transcript as she said it."
                ),
            },
            "felt_at": {
                "type": "string",
                "description": (
                    "When this state was FELT, if different from now: user-local "
                    "YYYY-MM-DDTHH:MM. 'This morning I was shit' said at noon → today ~09:00; "
                    "'yesterday I crashed after work' → yesterday ~17:00. Omit for right now. "
                    "If one message describes several states at different times, call this "
                    "tool once per state with its own felt_at."
                ),
            },
            "energy": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "Energy derived from her words: 1 empty/shutdown, 3 okay, 5 on top of the world. Omit if she said nothing about energy.",
            },
            "mood": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "Mood derived from her words: 1 awful, 3 neutral, 5 great. Omit if unclear. Mood and energy are independent — 'good mood, sleepy' is mood 4, energy 2.",
            },
            "meds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "e.g. 'dex'"},
                        "time": {"type": "string", "description": "Local HH:MM if she said when; omit otherwise."},
                    },
                    "required": ["name"],
                },
                "description": "Medication she mentions taking.",
            },
            "sleep_hours": {
                "type": "number",
                "description": "Hours slept last night, if mentioned.",
            },
            "sleep_quality": {
                "type": "string",
                "description": "Her description of sleep quality, if mentioned: 'badly', 'great', etc.",
            },
            "period": {
                "type": "string", "enum": ["started", "ended"],
                "description": "If she says her period started or ended.",
            },
        },
        "required": ["note"],
    },
}

DELETE_ENTRY_TOOL: dict = {
    "name": "delete_entry",
    "description": (
        "Erase a record that should never have existed: a duplicate task, a wrong "
        "tracking entry (state or meds/sleep/period event), a test or mis-capture. "
        "Completely removes it — use ONLY for mistakes, never for decisions: "
        "a task the user decided against gets update_task status='dropped' instead. "
        "Corrections are delete + re-log. Get IDs from second_brain_get first. "
        "Erasing a capture does NOT erase tasks extracted from it — erase those "
        "by their own ids. Deleting a task also deletes reminders attached to it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "UUID of the record to erase."},
        },
        "required": ["entry_id"],
    },
}

CLEANUP_SESSION_TOOL: dict = {
    "name": "cleanup_session",
    "description": (
        "Manage the periodic cleanup — reviewing unassigned captures and organising them. "
        "action='inbox': return unassigned captures ready for review. "
        "action='suggest_efforts': get AI suggestions for recurring themes worth naming as Efforts. "
        "action='assign': assign a capture to an effort or archive it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inbox", "suggest_efforts", "assign"],
            },
            "capture_id": {
                "type": "string",
                "description": "Required for action='assign'.",
            },
            "effort_id": {
                "type": "string",
                "description": "For action='assign': effort to assign to. Omit to archive instead.",
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Handlers — (user_id, input_dict, now, *, services) -> str
# ---------------------------------------------------------------------------

def handle_brain_dump(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    brain_dump_service,
) -> str:
    raw = str(input_dict.get("text", "")).strip()
    if not raw:
        return "No text provided — nothing to capture."

    result = brain_dump_service.process(user_id, raw, now)

    if result.synthesis is None:
        return (
            f"Saved your dump (synthesis unavailable right now, raw preserved).\n"
            f"Capture ID: {result.capture.id}"
        )

    syn = result.synthesis
    parts = [f"Saved: {syn.summary}"]

    if syn.cleaned_text:
        parts.append(f"\n{syn.cleaned_text}")

    if result.tasks_created:
        task_lines = ["\nTasks created:"]
        for t in result.tasks_created:
            due = f" (due {_fmt_datetime(t.due_at)})" if t.due_at else ""
            task_lines.append(f"  • {t.title}{due} [{t.priority}/{t.energy}]")
        parts.append("".join(task_lines))

    if syn.questions:
        parts.append("\nOpen questions:\n" + "\n".join(f"  ? {q}" for q in syn.questions))

    if syn.effort_hints:
        parts.append(
            "\nThis might be worth naming an Effort: "
            + ", ".join(syn.effort_hints)
        )

    return "\n".join(parts)


def handle_second_brain_get(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
    goal_service,
    capture_service,
    effort_service,
    reminder_service,
    state_service,
) -> str:
    what = str(input_dict.get("what", ""))

    if what == "tasks":
        tasks = task_service.list_open(user_id)
        parked = task_service.list_parked(user_id)
        if not tasks and not parked:
            return "No open tasks."
        overdue = [t for t in tasks if t.is_overdue(now)]
        rest = [t for t in tasks if not t.is_overdue(now)]
        lines = []
        if overdue:
            lines.append("OVERDUE:")
            for t in overdue:
                lines.append(f"  [{t.id}] {t.title} — due {_fmt_datetime(t.due_at)} | {t.priority}/{t.energy}")
        if rest:
            lines.append("Open:")
            for t in rest:
                due = f" — due {_fmt_datetime(t.due_at)}" if t.due_at else ""
                lines.append(f"  [{t.id}] {t.title}{due} | {t.priority}/{t.energy}")
        if parked:
            lines.append("Parked (not now, on the shelf):")
            for t in parked:
                lines.append(f"  [{t.id}] {t.title}")
        return "\n".join(lines)

    if what == "seeds":
        seeds = task_service.list_seeds(user_id)
        if not seeds:
            return "No seeds planted yet."
        lines = ["Seeds (no obligation, pick what sparks):"]
        for t in seeds:
            lines.append(f"  [{t.id}] {t.title} | energy {t.energy}")
        return "\n".join(lines)

    if what == "goals":
        goals = goal_service.list_active(user_id)
        if not goals:
            return "No active goals."
        return "\n".join(f"  [{g.id}] {g.summary()}" for g in goals)

    if what == "inbox":
        captures = capture_service.list_unassigned(user_id)
        if not captures:
            return "Inbox is clear — no unassigned captures."
        lines = [f"Unassigned captures ({len(captures)}):"]
        for c in captures:
            date_str = c.created_at.strftime("%d %b")
            summary = c.summary or c.raw[:60]
            lines.append(f"  [{c.id}] {date_str} — {summary}")
        return "\n".join(lines)

    if what == "efforts":
        efforts = effort_service.list_all(user_id)
        if not efforts:
            return "No efforts yet. These emerge from cleanup sessions."
        lines = []
        for e in efforts:
            lines.append(f"  [{e.id}] {e.title} ({e.intensity.value})")
        return "\n".join(lines)

    if what == "reminders":
        upcoming = reminder_service.upcoming(user_id, hours=48, now=now)
        recent = [r for r in reminder_service.recent(user_id, limit=10) if r.status != "scheduled"]
        lines = []
        if upcoming:
            lines.append("Scheduled:")
            lines.extend(f"  [{r.id}] {r.label} @ {_fmt_datetime(r.remind_at)}" for r in upcoming)
        if recent:
            lines.append("Recent (delivery status):")
            lines.extend(f"  {r.label} @ {_fmt_datetime(r.remind_at)} — {r.status}" for r in recent)
        return "\n".join(lines) if lines else "No reminders scheduled and none recently fired."

    if what == "tracking":
        states = state_service.recent_states(user_id, days=7, now=now)
        events = state_service.recent_events(user_id, days=7, now=now)
        if not states and not events:
            return "No tracking entries in the last 7 days."
        lines = []
        if states:
            lines.append("State logs (last 7 days):")
            for s in states:
                scores = "/".join(p for p in (
                    f"e{s.energy}" if s.energy else "", f"m{s.mood}" if s.mood else "",
                ) if p)
                felt = s.felt_at.strftime("%d %b %H:%M")
                lines.append(f"  [{s.id}] {felt} {scores or '·'} — {s.note[:80]}")
        if events:
            lines.append("Events:")
            for e in events:
                bits = [str(e.event_type)]
                if e.detail:
                    bits.append(e.detail)
                if e.value is not None:
                    bits.append(f"{e.value:g}")
                lines.append(f"  [{e.id}] {e.occurred_at.strftime('%d %b %H:%M')} — {' '.join(bits)}")
        return "\n".join(lines)

    return f"Unknown option: {what!r}. Use: tasks, goals, inbox, efforts, reminders, tracking."


def handle_create_task(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
) -> str:
    title = str(input_dict.get("title", "")).strip()
    if not title:
        return "Task title is required."
    try:
        priority = TaskPriority(input_dict.get("priority", "medium"))
    except ValueError:
        priority = TaskPriority.MEDIUM
    try:
        energy = TaskEnergy(input_dict.get("energy", "medium"))
    except ValueError:
        energy = TaskEnergy.MEDIUM
    from trellis.domain_second_brain_models import TaskKind
    try:
        kind = TaskKind(input_dict.get("kind", "todo"))
    except ValueError:
        kind = TaskKind.TODO
    task = task_service.create(
        user_id, title,
        kind=kind,
        priority=priority,
        energy=energy,
        description=input_dict.get("description"),
        due=input_dict.get("due"),
        now=now,
    )
    due = f" — due {_fmt_datetime(task.due_at)}" if task.due_at else ""
    return f"Task created: {task.title}{due} [{task.id}]"


def handle_complete_task(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
) -> str:
    task_id_str = str(input_dict.get("task_id", "")).strip()
    if not task_id_str:
        return "task_id is required."
    try:
        task_id = UUID(task_id_str)
    except ValueError:
        return f"Invalid task_id: {task_id_str!r}"
    try:
        task = task_service.complete(user_id, task_id, now=now)
        return f"Done: {task.title}"
    except TaskNotFoundError:
        return f"Task not found: {task_id_str}"


def handle_update_task(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
) -> str:
    task_id_str = str(input_dict.get("task_id", "")).strip()
    if not task_id_str:
        return "task_id is required."
    try:
        task_id = UUID(task_id_str)
    except ValueError:
        return f"Invalid task_id: {task_id_str!r}"

    kwargs: dict[str, Any] = {}
    if "title" in input_dict:
        kwargs["title"] = str(input_dict["title"]).strip()
    if "priority" in input_dict:
        try:
            kwargs["priority"] = TaskPriority(input_dict["priority"])
        except ValueError:
            pass
    if "energy" in input_dict:
        try:
            kwargs["energy"] = TaskEnergy(input_dict["energy"])
        except ValueError:
            pass
    if "status" in input_dict:
        from trellis.domain_second_brain_models import TaskStatus
        if input_dict["status"] in ("open", "dropped", "parked"):
            kwargs["status"] = TaskStatus(input_dict["status"])
    if "kind" in input_dict:
        from trellis.domain_second_brain_models import TaskKind
        try:
            kwargs["kind"] = TaskKind(input_dict["kind"])
        except ValueError:
            pass
    if "due" in input_dict:
        kwargs["due"] = input_dict["due"]
    if "description" in input_dict:
        kwargs["description"] = input_dict["description"]

    try:
        task = task_service.update(user_id, task_id, **kwargs, now=now)
        return f"Updated: {task.title}"
    except TaskNotFoundError:
        return f"Task not found: {task_id_str}"


def handle_set_reminder(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    reminder_service,
    tz,
) -> str:
    label = str(input_dict.get("label", "")).strip()
    remind_at_str = str(input_dict.get("remind_at", "")).strip()
    if not label or not remind_at_str:
        return "label and remind_at are both required."
    try:
        remind_at = datetime.fromisoformat(remind_at_str.replace("Z", "+00:00"))
    except ValueError:
        return f"Invalid remind_at format: {remind_at_str!r}. Use YYYY-MM-DDTHH:MM."
    # Timezone math is Python's job, never Claude's: a naive datetime is the
    # user's local wall-clock time.
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=tz)

    task_id = None
    if "task_id" in input_dict and input_dict["task_id"]:
        try:
                task_id = UUID(str(input_dict["task_id"]))
        except ValueError:
            pass

    recur = bool(input_dict.get("recur_daily", False))
    reminder = reminder_service.set(user_id, label, remind_at, task_id=task_id, recur_daily=recur, now=now)
    return f"Reminder set: {reminder.label} @ {_fmt_datetime(reminder.remind_at)} [{reminder.id}]"


def handle_cancel_reminder(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    reminder_service,
) -> str:
    rid_str = str(input_dict.get("reminder_id", "")).strip()
    if not rid_str:
        return "reminder_id is required."
    try:
        reminder_service.cancel(UUID(rid_str))
        return "Reminder cancelled."
    except ValueError:
        return f"Invalid reminder_id: {rid_str!r}"


def handle_add_goal(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    goal_service,
) -> str:
    title = str(input_dict.get("title", "")).strip()
    goal_type_str = str(input_dict.get("goal_type", "")).strip()
    if not title or not goal_type_str:
        return "title and goal_type are required."
    try:
        goal_type = GoalType(goal_type_str)
    except ValueError:
        return f"Unknown goal_type: {goal_type_str!r}. Use: race, aerobic, strength, life, habit, general."

    target_date = None
    if input_dict.get("target_date"):
        try:
            target_date = date.fromisoformat(str(input_dict["target_date"]))
        except ValueError:
            return f"Invalid target_date: {input_dict['target_date']!r}. Use YYYY-MM-DD."

    goal = goal_service.add(
        user_id, title, goal_type,
        target_date=target_date,
        is_fixed_date=bool(input_dict.get("is_fixed_date", False)),
        notes=input_dict.get("notes"),
        now=now,
    )
    return f"Goal added: {goal.summary()} [{goal.id}]"


def handle_update_goal(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    goal_service,
) -> str:
    goal_id_str = str(input_dict.get("goal_id", "")).strip()
    if not goal_id_str:
        return "goal_id is required."
    try:
        goal_id = UUID(goal_id_str)
    except ValueError:
        return f"Invalid goal_id: {goal_id_str!r}"

    kwargs: dict[str, Any] = {}
    if "title" in input_dict:
        kwargs["title"] = str(input_dict["title"]).strip()
    if "target_date" in input_dict and input_dict["target_date"]:
        try:
            kwargs["target_date"] = date.fromisoformat(str(input_dict["target_date"]))
        except ValueError:
            return f"Invalid target_date format."
    if "is_fixed_date" in input_dict:
        kwargs["is_fixed_date"] = bool(input_dict["is_fixed_date"])
    if "notes" in input_dict:
        kwargs["notes"] = input_dict["notes"]
    if "status" in input_dict:
        try:
            kwargs["status"] = GoalStatus(input_dict["status"])
        except ValueError:
            return f"Unknown status: {input_dict['status']!r}."

    try:
        goal = goal_service.update(user_id, goal_id, **kwargs, now=now)
        return f"Goal updated: {goal.summary()}"
    except GoalNotFoundError:
        return f"Goal not found: {goal_id_str}"


def handle_log_state(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    state_service,
    tz,
) -> str:
    from trellis.domain_second_brain_models import TrackingEventType

    note = str(input_dict.get("note", "")).strip()
    if not note:
        return "note is required — her words about how she's doing."

    energy = input_dict.get("energy")
    mood = input_dict.get("mood")
    parts: list[str] = []

    felt_at = None
    felt_str = str(input_dict.get("felt_at", "")).strip()
    if felt_str:
        try:
            felt_at = datetime.fromisoformat(felt_str)
            if felt_at.tzinfo is None:
                felt_at = felt_at.replace(tzinfo=tz)
        except ValueError:
            felt_at = None

    log = state_service.log_state(
        user_id, note,
        energy=int(energy) if energy is not None else None,
        mood=int(mood) if mood is not None else None,
        now=now,
        felt_at=felt_at,
    )
    scores = ", ".join(
        s for s in (
            f"energy {log.energy}" if log.energy else "",
            f"mood {log.mood}" if log.mood else "",
        ) if s
    )
    parts.append(f"State logged{f' ({scores})' if scores else ''}.")

    for med in input_dict.get("meds") or []:
        if not isinstance(med, dict) or not med.get("name"):
            continue
        occurred = now
        time_str = str(med.get("time", "")).strip()
        if time_str:
            try:
                h, m = time_str.split(":")
                occurred = now.astimezone(tz).replace(
                    hour=int(h), minute=int(m), second=0, microsecond=0
                )
            except (ValueError, AttributeError):
                pass
        state_service.log_event(
            user_id, TrackingEventType.MEDS,
            detail=str(med["name"]).strip(), occurred_at=occurred,
        )
        parts.append(f"Meds logged: {med['name']}{f' at {time_str}' if time_str else ''}.")

    sleep_hours = input_dict.get("sleep_hours")
    sleep_quality = input_dict.get("sleep_quality")
    if sleep_hours is not None or sleep_quality:
        state_service.log_event(
            user_id, TrackingEventType.SLEEP,
            detail=str(sleep_quality).strip() if sleep_quality else None,
            value=float(sleep_hours) if sleep_hours is not None else None,
            occurred_at=now,
        )
        parts.append("Sleep logged.")

    period = input_dict.get("period")
    if period in ("started", "ended"):
        state_service.log_event(
            user_id,
            TrackingEventType.PERIOD_START if period == "started" else TrackingEventType.PERIOD_END,
            occurred_at=now,
        )
        parts.append(f"Period {period}.")

    summary = state_service.today_summary(user_id, now)
    if summary:
        parts.append(summary)
    return " ".join(parts)


def handle_delete_entry(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    state_service,
    task_service,
    capture_service,
) -> str:
    entry_id_str = str(input_dict.get("entry_id", "")).strip()
    if not entry_id_str:
        return "entry_id is required."
    try:
        entry_id = UUID(entry_id_str)
    except ValueError:
        return f"Invalid entry_id: {entry_id_str!r}"
    if (
        state_service.delete_entry(user_id, entry_id)
        or task_service.delete(user_id, entry_id)
        or capture_service.delete(user_id, entry_id)
    ):
        return "Erased."
    return "No record with that id."


SAVE_TO_EFFORT_TOOL: dict = {
    "name": "save_to_effort",
    "description": (
        "Keep research, notes, or findings onto an Effort — an area the user is "
        "actively exploring (its own page in her vault that accumulates over time). "
        "Use this the moment there's something worth keeping from a research "
        "conversation, instead of offering to 'save to a seed'. Finds the effort "
        "by name or creates it if new — so a seed graduating into real exploration "
        "gets a home. If this came from a seed, pass graduated_seed_id to retire "
        "the seed (it's an effort now)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "effort_title": {
                "type": "string",
                "description": "Short, evocative name for the area, e.g. 'Making Music'. Reuse the exact name to add to an existing effort.",
            },
            "content": {
                "type": "string",
                "description": "The research/notes to keep — the full digest, links and all. Markdown is fine.",
            },
            "graduated_seed_id": {
                "type": "string",
                "description": "UUID of the seed this grew from, if any — it gets retired.",
            },
        },
        "required": ["effort_title", "content"],
    },
}

WEB_SEARCH_TOOL: dict = {
    "name": "web_search",
    "description": (
        "Search the web for current information. Use it to research a seed "
        "(\"look into that drum machine seed\" → search, bring back real options "
        "and links), answer a factual question, or find something concrete like "
        "local classes or prices. Read-only — it fetches, it can't act. Present "
        "results as a short digest with the links, not a wall of text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for. Be specific."},
        },
        "required": ["query"],
    },
}

RECALL_TOOL: dict = {
    "name": "recall",
    "description": (
        "Search the user's OWN second brain by MEANING, not keywords — surfaces past "
        "captures, efforts and seeds related to a query even when they share no words. "
        "Use when she asks 'what have I noted about X', 'have I thought about this "
        "before', 'what relates to this', or when a new idea might echo an existing "
        "effort or seed worth connecting. This is memory recall, not web_search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The idea or topic to find related notes for. A phrase or sentence works better than a single word.",
            },
        },
        "required": ["query"],
    },
}


def handle_save_to_effort(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    effort_service,
    capture_service,
    task_service,
) -> str:
    title = str(input_dict.get("effort_title", "")).strip()
    content = str(input_dict.get("content", "")).strip()
    if not title or not content:
        return "effort_title and content are both required."
    effort = effort_service.find_or_create(user_id, title, now)
    capture_service.save_research(user_id, content, effort_id=effort.id, now=now)

    retired = ""
    seed_id = str(input_dict.get("graduated_seed_id", "")).strip()
    if seed_id:
        try:
            from trellis.domain_second_brain_models import TaskStatus
            task_service.update(user_id, UUID(seed_id), status=TaskStatus.DROPPED, now=now)
            retired = " (seed retired — it's an effort now)"
        except (ValueError, Exception):
            pass
    return f"Saved to effort '{effort.title}'{retired}. It's on your {effort.title} page."


def handle_web_search(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    web_search,
) -> str:
    query = str(input_dict.get("query", "")).strip()
    if not query:
        return "query is required."
    result = web_search.search(query)
    if result is None:
        return "Search came back empty or the search service is unavailable — try rephrasing, or again in a moment."
    lines = []
    if result.answer:
        lines.append(result.answer)
        lines.append("")
    for r in result.results:
        snippet = r.snippet[:200] + ("…" if len(r.snippet) > 200 else "")
        lines.append(f"- {r.title} — {r.url}\n  {snippet}")
    return "\n".join(lines) if lines else "Nothing useful found."


def handle_recall(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    memory,
) -> str:
    query = str(input_dict.get("query", "")).strip()
    if not query:
        return "query is required."
    matches = memory.recall(user_id, query)
    if matches is None:
        return "Semantic recall is unavailable right now (embeddings aren't configured)."
    if not matches:
        return "Nothing in your second brain relates closely to that yet."
    lines = ["Related in your second brain:"]
    for m in matches:
        label = m.content[:80] + ("…" if len(m.content) > 80 else "")
        pct = round(m.similarity * 100)
        lines.append(f"  [{m.entity_id}] ({m.kind}, ~{pct}%) {label}")
    return "\n".join(lines)


def handle_cleanup_session(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    capture_service,
    cleanup_service,
) -> str:
    action = str(input_dict.get("action", "")).strip()

    if action == "inbox":
        captures = cleanup_service.inbox(user_id)
        if not captures:
            return "Inbox is clear — no unassigned captures to review."
        lines = [f"Inbox: {len(captures)} unassigned capture(s)\n"]
        for c in captures:
            date_str = c.created_at.strftime("%d %b")
            summary = c.summary or c.raw[:80]
            lines.append(f"[{c.id}] {date_str}\n  {summary}\n")
        return "\n".join(lines)

    if action == "suggest_efforts":
        suggestions = cleanup_service.suggest_efforts(user_id)
        if not suggestions:
            return "No recurring themes spotted yet — keep capturing and check back later."
        lines = ["Effort suggestions:"]
        for s in suggestions:
            lines.append(f"\n  {s.title} ({s.intensity})\n  {s.rationale}")
        return "\n".join(lines)

    if action == "assign":
        capture_id_str = str(input_dict.get("capture_id", "")).strip()
        if not capture_id_str:
            return "capture_id is required for action='assign'."
        try:
            capture_id = UUID(capture_id_str)
        except ValueError:
            return f"Invalid capture_id: {capture_id_str!r}"

        effort_id_str = input_dict.get("effort_id")
        if effort_id_str:
            try:
                effort_id = UUID(str(effort_id_str))
                capture_service.assign(capture_id, effort_id)
                return "Capture assigned to effort."
            except ValueError:
                return f"Invalid effort_id: {effort_id_str!r}"
        else:
            capture_service.archive(capture_id)
            return "Capture archived."

    return f"Unknown action: {action!r}. Use: inbox, suggest_efforts, assign."


# ---------------------------------------------------------------------------
# Context loader — Tier 1b (loaded when second brain is active domain)
# ---------------------------------------------------------------------------

def second_brain_context_loader(
    task_service,
    goal_service,
    effort_service,
) -> ContextLoader:
    """
    Tier 1b context for the second brain domain.
    Returns active goals + open task summary + efforts.
    Loaded when second_brain is the routed domain.
    """
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = []

        try:
            goals = goal_service.list_active(user_id)
            if goals:
                parts.append("Active goals:\n" + "\n".join(f"  {g.summary()}" for g in goals))
        except Exception:
            _log.warning("second_brain_context: goals load failed", exc_info=True)

        try:
            efforts = effort_service.list_all(user_id)
            if efforts:
                by_intensity = effort_service.summary_for_context(user_id)
                if by_intensity:
                    parts.append("Efforts:\n" + by_intensity)
        except Exception:
            _log.warning("second_brain_context: efforts load failed", exc_info=True)

        try:
            tasks = task_service.list_open(user_id)
            overdue = [t for t in tasks if t.is_overdue(now)]
            due_today = task_service.due_today(user_id, now)
            due_today_ids = {t.id for t in due_today}
            remaining = [t for t in tasks if t.id not in {t.id for t in overdue} and t.id not in due_today_ids]

            task_parts: list[str] = []
            if overdue:
                task_parts.append(
                    "Overdue:\n" + "\n".join(f"  [{t.id}] {t.title}" for t in overdue)
                )
            if due_today:
                task_parts.append(
                    "Due today:\n" + "\n".join(f"  [{t.id}] {t.title}" for t in due_today)
                )
            if remaining:
                task_parts.append(f"Open ({len(remaining)}): " + ", ".join(t.title for t in remaining[:5]))
            seeds = task_service.list_seeds(user_id)
            if seeds:
                task_parts.append(f"Seeds waiting ({len(seeds)}): " + ", ".join(t.title for t in seeds[:4]))
            if task_parts:
                parts.append("Tasks:\n" + "\n".join(task_parts))
        except Exception:
            _log.warning("second_brain_context: tasks load failed", exc_info=True)

        if not parts:
            return None
        return "[Second Brain]\n" + "\n\n".join(parts)

    return loader


def second_brain_snapshot(
    task_service,
    reminder_service,
    state_service=None,
) -> ContextLoader:
    """
    Tier 2 snapshot contribution — existence/urgency only, always loaded.
    Returns a compact line like: Tasks: 3 overdue, 2 due today | Reminders: 1 in 4h
    """
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = []

        try:
            tasks = task_service.list_open(user_id)
            overdue_count = sum(1 for t in tasks if t.is_overdue(now))
            today_count = len(task_service.due_today(user_id, now))
            total = len(tasks)
            task_str = f"Tasks: {total} open"
            if overdue_count:
                task_str += f", {overdue_count} overdue"
            if today_count:
                task_str += f", {today_count} due today"
            parts.append(task_str)
        except Exception:
            _log.warning("second_brain_snapshot: tasks failed", exc_info=True)

        try:
            soon = reminder_service.upcoming(user_id, hours=4, now=now)
            if soon:
                parts.append(f"Reminders: {len(soon)} due in 4h")
        except Exception:
            _log.warning("second_brain_snapshot: reminders failed", exc_info=True)

        if state_service is not None:
            try:
                state_line = state_service.today_summary(user_id, now)
                if state_line:
                    parts.append(state_line)
                cycle = state_service.cycle_day(user_id, now)
                if cycle is not None:
                    parts.append(f"Cycle day {cycle}")
            except Exception:
                _log.warning("second_brain_snapshot: state failed", exc_info=True)

        return " | ".join(parts) if parts else None

    return loader


# ---------------------------------------------------------------------------
# Routing signals — used by the domain router
# ---------------------------------------------------------------------------

SECOND_BRAIN_SIGNALS: list[str] = [
    "brain dump", "dump", "idea", "note", "capture", "remember",
    "task", "tasks", "todo", "to do", "to-do",
    "remind", "reminder", "appointment",
    "goal", "goals", "efforts", "inbox", "cleanup",
    "thought", "thinking about", "wondering", "what if",
    "question", "looking for", "find", "save this",
    "plan", "project", "working on",
]

# Self-description for semantic routing (the "big brain" / default room). Written
# to be embedded and matched against a message by MEANING, not keywords — so it
# lists the KINDS of things this room handles.
SECOND_BRAIN_DESCRIPTION: str = (
    "The everyday second brain and front door. Capturing thoughts and brain dumps; "
    "tasks and to-dos; reminders and appointments; ideas, notes and questions; goals "
    "and projects; life admin and general planning. Also tracking how you're doing day "
    "to day — mood, energy, medication, sleep, and period or cycle. General chat, "
    "reflection, and anything not clearly about a specialist area lands here."
)


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def second_brain_tools(
    task_service,
    goal_service,
    capture_service,
    effort_service,
    reminder_service,
    cleanup_service,
    state_service,
    web_search,
    tz,
) -> list[tuple[dict, Any]]:
    # brain_dump is NOT here — it's an always-available tool wired in core_main.
    tools = [
        (
            SECOND_BRAIN_GET_TOOL,
            lambda uid, inp, now: handle_second_brain_get(
                uid, inp, now,
                task_service=task_service,
                goal_service=goal_service,
                capture_service=capture_service,
                effort_service=effort_service,
                reminder_service=reminder_service,
                state_service=state_service,
            ),
        ),
        (
            CREATE_TASK_TOOL,
            lambda uid, inp, now: handle_create_task(uid, inp, now, task_service=task_service),
        ),
        (
            COMPLETE_TASK_TOOL,
            lambda uid, inp, now: handle_complete_task(uid, inp, now, task_service=task_service),
        ),
        (
            UPDATE_TASK_TOOL,
            lambda uid, inp, now: handle_update_task(uid, inp, now, task_service=task_service),
        ),
        (
            SET_REMINDER_TOOL,
            lambda uid, inp, now: handle_set_reminder(uid, inp, now, reminder_service=reminder_service, tz=tz),
        ),
        (
            CANCEL_REMINDER_TOOL,
            lambda uid, inp, now: handle_cancel_reminder(uid, inp, now, reminder_service=reminder_service),
        ),
        (
            ADD_GOAL_TOOL,
            lambda uid, inp, now: handle_add_goal(uid, inp, now, goal_service=goal_service),
        ),
        (
            UPDATE_GOAL_TOOL,
            lambda uid, inp, now: handle_update_goal(uid, inp, now, goal_service=goal_service),
        ),
        (
            DELETE_ENTRY_TOOL,
            lambda uid, inp, now: handle_delete_entry(
                uid, inp, now, state_service=state_service,
                task_service=task_service, capture_service=capture_service,
            ),
        ),
        (
            CLEANUP_SESSION_TOOL,
            lambda uid, inp, now: handle_cleanup_session(
                uid, inp, now,
                capture_service=capture_service,
                cleanup_service=cleanup_service,
            ),
        ),
        (
            SAVE_TO_EFFORT_TOOL,
            lambda uid, inp, now: handle_save_to_effort(
                uid, inp, now,
                effort_service=effort_service,
                capture_service=capture_service,
                task_service=task_service,
            ),
        ),
    ]
    # Web search only appears if a provider is configured — no dead tool otherwise.
    # (recall is Trellis-wide, so it's registered as an always-available tool in
    # core_main, not here — like brain_dump.)
    if web_search is not None:
        tools.append((
            WEB_SEARCH_TOOL,
            lambda uid, inp, now: handle_web_search(uid, inp, now, web_search=web_search),
        ))
    return tools


def _fmt_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d %b %H:%M UTC")
