"""
Tool schemas and handlers for the focus domain.

Handler signature: (user_id, input_dict, now) -> str
Context loaders: focus_context_loader, focus_snapshot
Registration: focus_tools(...)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_focus_models import (
    GoalStatus,
    GoalType,
    TaskEnergy,
    TaskPriority,
)
from trellis.domain_focus_service import GoalNotFoundError, TaskNotFoundError

_log = logging.getLogger(__name__)

# (user_id, now) -> context string or None
ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas — Claude tools API format
# ---------------------------------------------------------------------------

FOCUS_ADD_TOOL: dict = {
    "name": "focus_add",
    "description": (
        "Create one Focus record: a task, seed, goal, reminder, or a note onto "
        "an effort. Pick 'what', then send only that entity's fields (marked "
        "per-field below). Results warn about same-named duplicates — read them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["task", "goal", "reminder", "effort_note"],
                "description": (
                    "task: todo or seed (kind field). goal: any life/training goal. "
                    "reminder: a timed nudge, one-off or recurring. "
                    "effort_note: keep content on an effort page (or file an "
                    "existing capture there via capture_id)."
                ),
            },
            "title": {"type": "string", "description": "task/goal: what it is. Required for both."},
            "kind": {
                "type": "string", "enum": ["todo", "seed"], "default": "todo",
                "description": "task: todo = obligation. seed = exploration, no due date, never nags.",
            },
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "task: default medium."},
            "energy": {
                "type": "string", "enum": ["low", "medium", "high"],
                "description": "task: mental/physical energy needed. low=routine, high=deep focus. Default medium.",
            },
            "description": {"type": "string", "description": "task: optional detail."},
            "due": {
                "type": "string",
                "description": (
                    "task: due date/time in the USER'S LOCAL time (YYYY-MM-DDTHH:MM "
                    "or YYYY-MM-DD). Resolve relative phrases from context. No "
                    "timezone conversion. Omit if no deadline."
                ),
            },
            "goal_type": {
                "type": "string",
                "enum": ["race", "aerobic", "strength", "life", "habit", "general"],
                "description": "goal: race/aerobic/strength feed the training module; life/habit/general are everything else. Required for goals.",
            },
            "target_date": {"type": "string", "description": "goal: YYYY-MM-DD. Omit if open-ended."},
            "is_fixed_date": {"type": "boolean", "description": "goal: true if the date cannot move (race day)."},
            "notes": {"type": "string", "description": "goal: optional notes."},
            "label": {"type": "string", "description": "reminder: what it's for. Required for reminders."},
            "remind_at": {
                "type": "string",
                "description": (
                    "reminder: USER-LOCAL YYYY-MM-DDTHH:MM, exactly the time they "
                    "said — no timezone conversion. Required for reminders."
                ),
            },
            "task_id": {"type": "string", "description": "reminder: optionally link to an existing task."},
            "recurrence": {
                "type": "string", "enum": ["daily", "weekly", "monthly", "yearly"],
                "description": "reminder: how it repeats ('every Sunday evening' -> weekly with remind_at on the next Sunday). Omit for a one-off.",
            },
            "effort_title": {
                "type": "string",
                "description": "effort_note: short evocative area name, e.g. 'Making Music'. Reuse the exact name to add to an existing effort. Required for effort_note.",
            },
            "content": {"type": "string", "description": "effort_note: the research/notes to keep — full digest, links and all."},
            "graduated_seed_id": {"type": "string", "description": "effort_note: seed this grew from, if any — it gets retired."},
            "capture_id": {"type": "string", "description": "effort_note: an existing capture (focus_get inbox) to file into the effort instead of content."},
        },
        "required": ["what"],
    },
}

FOCUS_UPDATE_TOOL: dict = {
    "name": "focus_update",
    "description": (
        "Change one existing Focus record by id: a task/seed, a goal, or a "
        "reminder. Pick 'what' + id, then send only the fields that change. "
        "Task: done -> status='done'; delete/remove -> status='dropped' (gone "
        "forever); shelve -> status='parked'; reclassify todo<->seed with kind. "
        "Goal: achieved -> status='achieved'. Reminder: the ONLY change is "
        "status='cancelled' (to move one, cancel it and focus_add a new one). "
        "Effort: the ONLY change is title (rename — its vault page moves with it)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["task", "goal", "reminder", "effort"]},
            "id": {"type": "string", "description": "UUID of the record (from focus_get)."},
            "title": {"type": "string", "description": "task/goal: new title."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "task."},
            "energy": {"type": "string", "enum": ["low", "medium", "high"], "description": "task."},
            "kind": {"type": "string", "enum": ["todo", "seed"], "description": "task: reclassify."},
            "status": {
                "type": "string",
                "enum": ["open", "done", "dropped", "parked", "active", "achieved", "paused", "cancelled"],
                "description": (
                    "task: open/done/dropped/parked. goal: active/achieved/paused/dropped. "
                    "reminder: cancelled only."
                ),
            },
            "due": {"type": "string", "description": "task: user-local YYYY-MM-DDTHH:MM or YYYY-MM-DD."},
            "description": {"type": "string", "description": "task."},
            "target_date": {"type": "string", "description": "goal: YYYY-MM-DD."},
            "is_fixed_date": {"type": "boolean", "description": "goal."},
            "notes": {"type": "string", "description": "goal: REPLACES stored notes — the result echoes what was overwritten."},
        },
        "required": ["what", "id"],
    },
}

BRAIN_DUMP_TOOL: dict = {
    "name": "brain_dump",
    "description": (
        "Capture anything the user wants to offload from their working memory — "
        "ideas, tasks, questions, things they want to remember, half-formed thoughts. "
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

FOCUS_GET_TOOL: dict = {
    "name": "focus_get",
    "description": "Retrieve your organised data (tasks, goals, captures, efforts, reminders). Use this before presenting tasks, goals, inbox captures, efforts, or reminders.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["tasks", "seeds", "goals", "inbox", "efforts", "effort", "reminders"],
                "description": (
                    "tasks: open todos ordered by urgency, plus parked. "
                    "seeds: the exploration menu — for 'what could I explore'. "
                    "goals: all active goals. "
                    "inbox: unassigned captures for cleanup. "
                    "efforts: all efforts by intensity. "
                    "effort: ONE effort's full page — every note filed on it, with ids "
                    "(pass name). Read it before advising on or reorganising a project. "
                    "reminders: ALL scheduled reminders (with ids + recurrence) + recent delivery status. "
                    "(wellbeing/tracking lives in the Sense room, in context there — not here.)"
                ),
            },
            "name": {"type": "string", "description": "effort: the effort's title."},
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

UPDATE_TASK_TOOL: dict = {
    "name": "update_task",
    "description": (
        "Update a task or seed: title, priority, energy, kind, due date, "
        "description, or status. Done → status='done'. Delete/remove → "
        "status='dropped' (gone from every view, never again). Shelve for "
        "later → status='parked' (visible in its own section). Reclassify "
        "todo↔seed with kind. Only send fields that change."
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
                "type": "string", "enum": ["open", "done", "dropped", "parked"],
                "description": "done = completed. dropped = never again, invisible. parked = not now, shelved but visible. open = back on the list.",
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
    "description": "Set a reminder. Works for appointments, time-sensitive tasks, and recurring nudges (daily, weekly, monthly or yearly).",
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
            "recurrence": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly", "yearly"],
                "description": (
                    "How it repeats, if it does: 'every Sunday evening' -> weekly "
                    "with remind_at on the next Sunday; 'monthly' fires the same "
                    "day each month (clamped for short months). Omit for a one-off."
                ),
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

DELETE_ENTRY_TOOL: dict = {
    "name": "delete_entry",
    "description": (
        "Erase a record that should never have existed: a duplicate task, a wrong "
        "tracking entry (state or meds/sleep/period event), a test or mis-capture. "
        "Completely removes it — use ONLY for mistakes, never for decisions: "
        "a task the user decided against gets update_task status='dropped' instead. "
        "Corrections are delete + re-log. Get IDs from focus_get first. "
        "Erasing a capture does NOT erase tasks extracted from it — erase those "
        "by their own ids. Deleting a task also deletes reminders attached to it. "
        "An EMPTY duplicate effort can be erased too (its page is removed); one "
        "with notes still on it is refused — move them first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "UUID of the record to erase."},
        },
        "required": ["entry_id"],
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
    tz,
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
            due = f" (due {_fmt_datetime(t.due_at, tz)})" if t.due_at else ""
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


def handle_focus_get(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
    goal_service,
    capture_service,
    effort_service,
    reminder_service,
    tz,
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
                lines.append(f"  [{t.id}] {t.title} — due {_fmt_datetime(t.due_at, tz)} | {t.priority}/{t.energy}")
        if rest:
            lines.append("Open:")
            for t in rest:
                due = f" — due {_fmt_datetime(t.due_at, tz)}" if t.due_at else ""
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
            date_str = c.created_at.astimezone(tz).strftime("%d %b")
            summary = c.summary or c.raw[:60]
            lines.append(f"  [{c.id}] {date_str} — {summary}")
        return "\n".join(lines)

    if what == "efforts":
        efforts = effort_service.list_all(user_id)
        if not efforts:
            return "No efforts yet. They grow from saved research and graduated seeds."
        lines = []
        for e in efforts:
            lines.append(f"  [{e.id}] {e.title} ({e.intensity.value})")
        return "\n".join(lines)

    if what == "effort":
        title = str(input_dict.get("name", "")).strip()
        if not title:
            return "name is required for what='effort'."
        page = effort_service.page(user_id, title, capture_service)
        if page is None:
            return f"No effort called '{title}'. focus_get what='efforts' lists them."
        effort, captures = page
        lines = [f"Effort: {effort.title} ({effort.intensity.value}) [{effort.id}]"]
        if effort.notes:
            lines.append(effort.notes)
        if not captures:
            lines.append("Nothing filed on it yet.")
        for c in captures:
            when = c.created_at.astimezone(tz).strftime("%d %b")
            body = (c.synthesis or c.raw or "").strip()
            lines.append(f"  [{c.id}] {when} — {body[:300]}")
        return "\n".join(lines)

    if what == "reminders":
        upcoming = reminder_service.all_scheduled(user_id)
        recent = [r for r in reminder_service.recent(user_id, limit=10) if r.status != "scheduled"]
        lines = []
        if upcoming:
            lines.append("Scheduled:")
            lines.extend(
                f"  [{r.id}] {r.label} @ {_fmt_datetime(r.remind_at, tz)}"
                + (f" (repeats {r.recurrence})" if r.recurrence else "")
                for r in upcoming
            )
        if recent:
            lines.append("Recent (delivery status):")
            lines.extend(f"  {r.label} @ {_fmt_datetime(r.remind_at, tz)} — {r.status}" for r in recent)
        return "\n".join(lines) if lines else "No reminders scheduled and none recently fired."

    return f"Unknown option: {what!r}. Use: tasks, seeds, goals, inbox, efforts, reminders."


def handle_create_task(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
    tz,
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
    from trellis.domain_focus_models import TaskKind
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
    due = f" — due {_fmt_datetime(task.due_at, tz)}" if task.due_at else ""
    result = f"Task created: {task.title}{due} [{task.id}]"
    try:
        dup = next(
            (t for t in task_service.list_open(user_id)
             if t.id != task.id and t.title.strip().lower() == title.lower()),
            None,
        )
        if dup is not None:
            result += (
                f"\nHeads up: an open task with this title already existed [{dup.id}]. "
                "If that makes this a duplicate, ask them which to drop."
            )
    except Exception:
        pass
    return result


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

    # status='done' routes through complete(): it owns completed_at, the task
    # event, and the vault refresh — a plain status write would skip all three.
    completed = None
    if input_dict.get("status") == "done":
        try:
            completed = task_service.complete(user_id, task_id, now=now)
        except TaskNotFoundError:
            return f"Task not found: {task_id_str}"

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
        from trellis.domain_focus_models import TaskStatus
        if input_dict["status"] in ("open", "dropped", "parked"):
            kwargs["status"] = TaskStatus(input_dict["status"])

    if "kind" in input_dict:
        from trellis.domain_focus_models import TaskKind
        try:
            kwargs["kind"] = TaskKind(input_dict["kind"])
        except ValueError:
            pass
    if "due" in input_dict:
        kwargs["due"] = input_dict["due"]
    if "description" in input_dict:
        kwargs["description"] = input_dict["description"]

    if completed is not None and not kwargs:
        return f"Done: {completed.title}"

    try:
        task = task_service.update(user_id, task_id, **kwargs, now=now)
    except TaskNotFoundError:
        return f"Task not found: {task_id_str}"
    if completed is not None:
        return f"Done + updated: {task.title}"
    return f"Updated: {task.title}"


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

    recurrence = input_dict.get("recurrence")
    if recurrence not in ("daily", "weekly", "monthly", "yearly"):
        recurrence = None
    # Duplicate guard: warn, never block — a same-label reminder is usually a
    # re-ask that already exists, and silent duplicates fire twice.
    dup = None
    try:
        dup = next(
            (r for r in reminder_service.all_scheduled(user_id)
             if r.label.strip().lower() == label.lower()),
            None,
        )
    except Exception:
        dup = None
    reminder = reminder_service.set(user_id, label, remind_at, task_id=task_id, recurrence=recurrence, now=now)
    repeats = f", repeats {reminder.recurrence}" if reminder.recurrence else ""
    result = f"Reminder set: {reminder.label} @ {_fmt_datetime(reminder.remind_at, tz)}{repeats} [{reminder.id}]"
    if dup is not None:
        dup_repeats = f", repeats {dup.recurrence}" if dup.recurrence else ""
        result += (
            f"\nHeads up: a scheduled reminder with this label already existed — "
            f"@ {_fmt_datetime(dup.remind_at, tz)}{dup_repeats} [{dup.id}]. "
            "If that makes this a duplicate, ask them which to cancel."
        )
    return result


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
    result = f"Goal added: {goal.summary()} [{goal.id}]"
    try:
        dup = next(
            (g for g in goal_service.list_active(user_id)
             if g.id != goal.id and g.title.strip().lower() == title.lower()),
            None,
        )
        if dup is not None:
            result += (
                f"\nHeads up: an active goal with this title already existed [{dup.id}]. "
                "If that makes this a duplicate, ask them which to drop."
            )
    except Exception:
        pass
    return result


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

    # Notes are a wholesale text replace — echo what got overwritten so a bad
    # rewrite is visible in the result, not silently gone.
    old_notes = None
    if "notes" in kwargs:
        try:
            old_notes = next(
                (g.notes for g in goal_service.list_active(user_id) if g.id == goal_id),
                None,
            )
        except Exception:
            old_notes = None

    try:
        goal = goal_service.update(user_id, goal_id, **kwargs, now=now)
    except GoalNotFoundError:
        return f"Goal not found: {goal_id_str}"
    result = f"Goal updated: {goal.summary()}"
    if old_notes and kwargs.get("notes") != old_notes:
        result += f'\nNotes replaced — they used to say: "{old_notes}"'
    return result


def handle_delete_entry(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    sense_service,
    task_service,
    capture_service,
    effort_service=None,
) -> str:
    entry_id_str = str(input_dict.get("entry_id", "")).strip()
    if not entry_id_str:
        return "entry_id is required."
    try:
        entry_id = UUID(entry_id_str)
    except ValueError:
        return f"Invalid entry_id: {entry_id_str!r}"
    if (
        sense_service.delete_entry(user_id, entry_id)
        or task_service.delete(user_id, entry_id)
        or capture_service.delete(user_id, entry_id)
    ):
        return "Erased."
    verdict = (effort_service.delete_if_empty(user_id, entry_id, capture_service)
               if effort_service is not None else "not_found")
    if verdict == "deleted":
        return "Erased (empty effort, page removed)."
    if verdict == "not_empty":
        return ("That effort still has notes filed on it — move them first "
                "(save_to_effort with capture_id), then erase.")
    return "No record with that id."


SAVE_TO_EFFORT_TOOL: dict = {
    "name": "save_to_effort",
    "description": (
        "Keep research, notes, or findings onto an Effort — an area the user is "
        "actively exploring (its own page in their vault that accumulates over time). "
        "Use this the moment there's something worth keeping from a research "
        "conversation, instead of offering to 'save to a seed'. Finds the effort "
        "by name or creates it if new — so a seed graduating into real exploration "
        "gets a home. If this came from a seed, pass graduated_seed_id to retire "
        "the seed (it's an effort now). To file an EXISTING inbox capture into an "
        "effort, pass capture_id instead of content."
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
            "capture_id": {
                "type": "string",
                "description": "UUID of an existing capture (from focus_get inbox) to file into this effort — instead of content.",
            },
        },
        "required": ["effort_title"],
    },
}

WEB_SEARCH_TOOL: dict = {
    "name": "web_search",
    "description": (
        "Search the outside world. source='web' (default): general search — "
        "research a seed, answer a factual question, find classes or prices. "
        "source='news': current events (the Guardian first when configured, "
        "then web news). source='pubmed': peer-reviewed medicine (NCBI). "
        "source='scholar': scholarly work across every field (OpenAlex). "
        "source='trials': registered clinical trials (ClinicalTrials.gov). "
        "All citation sources return REAL papers — title, venue, date, link — "
        "save keepers to a Learn map as kind='source'. Read-only — it fetches, it can't act. Present results "
        "as a short digest with the links, not a wall of text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for. Be specific. For pubmed, use topic terms (\"lisdexamfetamine menstrual cycle\"), not sentences."},
            "source": {
                "type": "string", "enum": ["web", "news", "pubmed", "scholar", "trials"],
                "description": "Where to look. Default web.",
            },
        },
        "required": ["query"],
    },
}

RECALL_TOOL: dict = {
    "name": "recall",
    "description": (
        "Search the user's OWN second brain by MEANING, not keywords — surfaces past "
        "captures, efforts and seeds related to a query even when they share no words. "
        "Use when they ask 'what have I noted about X', 'have I thought about this "
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


def handle_focus_add(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
    goal_service,
    reminder_service,
    effort_service,
    capture_service,
    tz,
) -> str:
    """Dispatch write: one door, the proven per-entity handlers behind it."""
    what = str(input_dict.get("what", "")).strip()
    if what == "task":
        return handle_create_task(user_id, input_dict, now, task_service=task_service, tz=tz)
    if what == "goal":
        return handle_add_goal(user_id, input_dict, now, goal_service=goal_service)
    if what == "reminder":
        return handle_set_reminder(user_id, input_dict, now, reminder_service=reminder_service, tz=tz)
    if what == "effort_note":
        return handle_save_to_effort(
            user_id, input_dict, now,
            effort_service=effort_service, capture_service=capture_service,
            task_service=task_service,
        )
    return f"Unknown what: {what!r}. Use: task, goal, reminder, effort_note."


def handle_focus_update(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
    goal_service,
    reminder_service,
    effort_service=None,
) -> str:
    """Dispatch write: routes to the per-entity update handlers by 'what'."""
    what = str(input_dict.get("what", "")).strip()
    rec_id = str(input_dict.get("id", "")).strip()
    if what == "task":
        return handle_update_task(
            user_id, {**input_dict, "task_id": rec_id}, now, task_service=task_service,
        )
    if what == "goal":
        return handle_update_goal(
            user_id, {**input_dict, "goal_id": rec_id}, now, goal_service=goal_service,
        )
    if what == "reminder":
        if input_dict.get("status") != "cancelled":
            return "Reminders only cancel (status='cancelled'). To move one: cancel it, then focus_add a new one."
        return handle_cancel_reminder(
            user_id, {"reminder_id": rec_id}, now, reminder_service=reminder_service,
        )
    if what == "effort":
        new_title = str(input_dict.get("title", "")).strip()
        if not new_title:
            return "title is required to rename an effort."
        try:
            renamed = effort_service.rename(user_id, UUID(rec_id), new_title)
        except ValueError:
            return f"Invalid id: {rec_id!r}"
        if renamed is None:
            return "No effort with that id."
        return f"Renamed to '{renamed.title}' — its vault page moved with it."
    return f"Unknown what: {what!r}. Use: task, goal, reminder, effort."


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
    capture_id_str = str(input_dict.get("capture_id", "")).strip()
    if not title:
        return "effort_title is required."
    if not content and not capture_id_str:
        return "Pass content (new material) or capture_id (an existing capture to file)."
    effort = effort_service.find_or_create(user_id, title, now)
    if capture_id_str:
        try:
            capture_service.assign(UUID(capture_id_str), effort.id)
        except ValueError:
            return f"Invalid capture_id: {capture_id_str!r}"
        return f"Filed that capture into '{effort.title}'. It's on your {effort.title} page."
    capture_service.save_research(user_id, content, effort_id=effort.id, now=now)

    retired = ""
    seed_id = str(input_dict.get("graduated_seed_id", "")).strip()
    if seed_id:
        try:
            from trellis.domain_focus_models import TaskStatus
            task_service.update(user_id, UUID(seed_id), status=TaskStatus.DROPPED, now=now)
            retired = " (seed retired — it's an effort now)"
        except Exception:
            _log.warning("save_to_effort: seed retirement failed", exc_info=True)
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
    source = str(input_dict.get("source", "web"))
    if source not in ("web", "news", "pubmed", "scholar", "trials"):
        source = "web"
    result = web_search.search(query, source=source)
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


# ---------------------------------------------------------------------------
# Context loader — Tier 1b (loaded when focus is active domain)
# ---------------------------------------------------------------------------

def focus_context_loader(
    task_service,
    goal_service,
    effort_service,
) -> ContextLoader:
    """
    Tier 1b context for the focus domain.
    Returns active goals + open task summary + efforts.
    Loaded when focus is the routed domain.
    """
    def loader(user_id: UUID, now: datetime) -> str | None:
        from trellis.domain_focus_claude import FOCUS_GUIDANCE
        parts: list[str] = [FOCUS_GUIDANCE]

        try:
            goals = goal_service.list_active(user_id)
            if goals:
                parts.append("Active goals:\n" + "\n".join(f"  {g.summary()}" for g in goals))
        except Exception:
            _log.warning("focus_context: goals load failed", exc_info=True)

        try:
            efforts = effort_service.list_all(user_id)
            if efforts:
                by_intensity = effort_service.summary_for_context(user_id)
                if by_intensity:
                    parts.append("Efforts:\n" + by_intensity)
        except Exception:
            _log.warning("focus_context: efforts load failed", exc_info=True)

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
            _log.warning("focus_context: tasks load failed", exc_info=True)

        if not parts:
            return None
        return "[Focus]\n" + "\n\n".join(parts)

    return loader


def focus_snapshot(
    task_service,
    reminder_service,
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
            _log.warning("focus_snapshot: tasks failed", exc_info=True)

        try:
            soon = reminder_service.upcoming(user_id, hours=4, now=now)
            if soon:
                parts.append(f"Reminders: {len(soon)} due in 4h")
        except Exception:
            _log.warning("focus_snapshot: reminders failed", exc_info=True)

        return " | ".join(parts) if parts else None

    return loader


# ---------------------------------------------------------------------------
# Routing signals — used by the domain router
# ---------------------------------------------------------------------------

FOCUS_SIGNALS: list[str] = [
    "brain dump", "dump", "idea", "note", "capture", "remember",
    "task", "tasks", "todo", "to do", "to-do",
    "remind", "reminder", "appointment",
    "goal", "goals", "efforts", "inbox", "cleanup",
    "thought", "thinking about", "wondering", "what if",
    "question", "looking for", "find", "save this",
    "plan", "project", "working on",
]

# The rooms inside the focus house, for semantic routing. Each phrase is embedded
# separately and the house scores by its BEST-matching room — so keep phrases
# short, concrete and 2+ words (single words are noise magnets). Growing the
# house = adding a room here; the router picks it up automatically. No catch-all
# room: generic chat falls through to the big brain (routes empty).
FOCUS_ROOMS: list[str] = [
    "tasks and to-dos",
    "life admin",
    "reminders and appointments",
    "capturing ideas and notes",
    "brain dumps",
    "shopping lists",
    "goals and projects",
    "organising things to do or remember",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def focus_tools(
    task_service,
    goal_service,
    capture_service,
    effort_service,
    reminder_service,
    sense_service,
    web_search,
    tz,
) -> list[tuple[dict, Any]]:
    # brain_dump is NOT here — it's an always-available tool wired in core_main.
    # sense_service is passed only for the cross-cutting delete_entry (which erases
    # tracking entries owned by Sense, as well as tasks/captures).
    tools = [
        (
            FOCUS_GET_TOOL,
            lambda uid, inp, now: handle_focus_get(
                uid, inp, now,
                task_service=task_service,
                goal_service=goal_service,
                capture_service=capture_service,
                effort_service=effort_service,
                reminder_service=reminder_service,
                tz=tz,
            ),
        ),
        (
            FOCUS_ADD_TOOL,
            lambda uid, inp, now: handle_focus_add(
                uid, inp, now,
                task_service=task_service,
                goal_service=goal_service,
                reminder_service=reminder_service,
                effort_service=effort_service,
                capture_service=capture_service,
                tz=tz,
            ),
        ),
        (
            FOCUS_UPDATE_TOOL,
            lambda uid, inp, now: handle_focus_update(
                uid, inp, now,
                task_service=task_service,
                goal_service=goal_service,
                reminder_service=reminder_service,
                effort_service=effort_service,
            ),
        ),
        (
            DELETE_ENTRY_TOOL,
            lambda uid, inp, now: handle_delete_entry(
                uid, inp, now, sense_service=sense_service,
                task_service=task_service, capture_service=capture_service,
                effort_service=effort_service,
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


def _fmt_datetime(dt: datetime | None, tz) -> str:
    """Render a stored (tz-aware) datetime in the USER'S timezone. Never label a
    local time as UTC — a wrong label invites the model to 'correct' it."""
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%d %b %H:%M")
