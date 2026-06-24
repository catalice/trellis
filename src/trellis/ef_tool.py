"""
Tool schemas and handlers for the executive function (EF) domain.

All tools follow the assembler handler signature: (user_id, input_dict, now) -> str.
Register with: registry.add_domain("ef", ..., ef_tools(...), EF_SIGNALS)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

CREATE_TASK_TOOL = {
    "name": "create_task",
    "description": (
        "Create a new task. Captures a to-do with optional priority, energy level, "
        "and due date. Use when the user asks to add, capture, or remember something she needs to do."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The task title."},
            "priority": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "medium", "high"]},
                    {"type": "null"},
                ],
                "description": "Task priority. Defaults to medium.",
            },
            "energy": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "medium", "high"]},
                    {"type": "null"},
                ],
                "description": "Energy required. Defaults to medium.",
            },
            "due_at": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Due date/time as ISO 8601 string (e.g. 2026-06-20T09:00:00Z).",
            },
        },
        "required": ["title"],
    },
}

COMPLETE_TASK_TOOL = {
    "name": "complete_task",
    "description": (
        "Mark a task as done. Match by title substring. Use when the user says she's done "
        "something, ticks off a task, or says 'done', 'finished', 'completed'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title or substring to match."},
        },
        "required": ["title"],
    },
}

LIST_TASKS_TOOL = {
    "name": "list_tasks",
    "description": "List open tasks. Optionally filter by priority or energy level.",
    "input_schema": {
        "type": "object",
        "properties": {
            "priority": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "medium", "high"]},
                    {"type": "null"},
                ],
            },
            "energy": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "medium", "high"]},
                    {"type": "null"},
                ],
            },
        },
        "required": [],
    },
}

SELECT_TODAY_TASKS_TOOL = {
    "name": "select_today_tasks",
    "description": (
        "Pick the best tasks for today based on current energy level. "
        "Returns a short list of the most relevant tasks. Use when the user asks "
        "what to focus on, what she should do today, or for a task suggestion."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "energy": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "medium", "high"]},
                    {"type": "null"},
                ],
                "description": "Current energy level to match tasks against.",
            },
            "limit": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "Max tasks to return. Defaults to 3.",
            },
        },
        "required": [],
    },
}

UPDATE_TASK_TOOL = {
    "name": "update_task",
    "description": (
        "Edit an existing task: rename it, change due date, update priority or energy level. "
        "Match by title reference. Only pass the fields you want to change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "Title substring to identify the task.",
            },
            "new_title": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "New title, if renaming.",
            },
            "due_at": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": (
                    "New due date as ISO 8601 string. Pass null to clear the due date. "
                    "Omit entirely to leave the due date unchanged."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Task priority level.",
            },
            "energy": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Energy required to do this task.",
            },
        },
        "required": ["reference"],
    },
}

ARCHIVE_TASK_TOOL = {
    "name": "archive_task",
    "description": (
        "Archive a task — remove it from the open list without completing it. "
        "Accepts a title substring or a number reference (e.g. '2' or '2, 3'). "
        "Use when the user drops, cancels, or no longer wants to track a task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "Task title substring or number(s) from the open list.",
            },
        },
        "required": ["reference"],
    },
}

SET_REMINDER_TOOL = {
    "name": "set_reminder",
    "description": (
        "Schedule a reminder at a specific time. Can be for a task (will link to it) "
        "or standalone (any label). Use when the user says 'remind me' or specifies a time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "What to remind about. For task reminders, use the task title substring. "
                    "For standalone reminders, use any descriptive label."
                ),
            },
            "remind_at": {
                "type": "string",
                "description": "When to send the reminder as ISO 8601 datetime string.",
            },
        },
        "required": ["label", "remind_at"],
    },
}

CANCEL_REMINDER_TOOL = {
    "name": "cancel_reminder",
    "description": (
        "Cancel a scheduled reminder. Match by label/task title substring. "
        "Use when the user says 'cancel reminder', 'remove reminder', or no longer wants to be reminded."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "Label or task title substring to find the reminder.",
            },
        },
        "required": ["reference"],
    },
}

LIST_REMINDERS_TOOL = {
    "name": "list_reminders",
    "description": "List all scheduled reminders, upcoming first.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

LIST_COMPLETED_TASKS_TOOL = {
    "name": "list_completed_tasks",
    "description": (
        "Show recently completed tasks. "
        "Use when the user asks what they've done, wants to review their wins, "
        "or asks about task history."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

RESPOND_TO_INSIGHT_TOOL = {
    "name": "respond_to_insight",
    "description": (
        "Respond to a pattern insight — snooze it (hide for 7 days), resolve it (acted on / no longer relevant), "
        "or reject it (false positive). Use when the user reacts to an insight shown in context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "insight_summary": {
                "type": "string",
                "description": "Substring of the insight summary to identify it.",
            },
            "action": {
                "type": "string",
                "enum": ["snooze", "resolve", "reject"],
                "description": "snooze = hide for 7 days; resolve = dealt with; reject = false positive.",
            },
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Optional note explaining the response.",
            },
        },
        "required": ["insight_summary", "action"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_create_task(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    from trellis.tasks import Energy, Priority

    priority_raw = input_dict.get("priority")
    energy_raw = input_dict.get("energy")
    due_at_raw = input_dict.get("due_at")

    priority = Priority(priority_raw) if priority_raw else Priority.MEDIUM
    energy = Energy(energy_raw) if energy_raw else Energy.MEDIUM

    due_at: datetime | None = None
    if due_at_raw:
        try:
            due_at = datetime.fromisoformat(due_at_raw)
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return f"Could not parse due date '{due_at_raw}'. Use ISO 8601 format (e.g. 2026-06-20T09:00:00Z)."

    try:
        task = task_service.create(user_id, input_dict["title"], priority=priority, energy=energy, due_at=due_at)
    except Exception as exc:
        from trellis.tasks import DuplicateTaskError
        if isinstance(exc, DuplicateTaskError):
            return f"That task already exists: '{exc.task.title}'."
        raise

    parts = [f"Task created: '{task.title}'", f"priority {task.priority}", f"energy {task.energy}"]
    if task.due_at:
        parts.append(f"due {task.due_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return " | ".join(parts)


def handle_complete_task(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    from trellis.tasks import AmbiguousTaskError, TaskNotFoundError

    title = input_dict["title"]
    try:
        task = task_service.complete(user_id, title)
        return f"Done: '{task.title}'"
    except TaskNotFoundError:
        return f"No open task found matching '{title}'."
    except AmbiguousTaskError as exc:
        titles = ", ".join(f"'{t.title}'" for t in exc.matches)
        return f"Multiple tasks matched '{title}': {titles}. Be more specific."


def handle_list_tasks(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    tasks = task_service.list_open(user_id)

    priority_filter = input_dict.get("priority")
    energy_filter = input_dict.get("energy")

    if priority_filter:
        tasks = [t for t in tasks if t.priority == priority_filter]
    if energy_filter:
        tasks = [t for t in tasks if t.energy == energy_filter]

    if not tasks:
        return "No open tasks." if not (priority_filter or energy_filter) else "No tasks matching those filters."

    lines = [f"Open tasks ({len(tasks)}):"]
    for i, t in enumerate(tasks, 1):
        line = f"  {i}. {t.title} | priority:{t.priority} | energy:{t.energy}"
        if t.due_at:
            if t.due_at < now:
                line += f" | OVERDUE (was {t.due_at.strftime('%Y-%m-%d %H:%M UTC')})"
            else:
                line += f" | due:{t.due_at.strftime('%Y-%m-%d %H:%M UTC')}"
        lines.append(line)
    return "\n".join(lines)


def handle_select_today_tasks(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    from trellis.tasks import Energy

    energy_raw = input_dict.get("energy")
    energy = Energy(energy_raw) if energy_raw else None
    limit = int(input_dict.get("limit") or 3)

    tasks = task_service.select_today(user_id, energy=energy, limit=limit)

    if not tasks:
        return "No open tasks to suggest."

    lines = [f"Suggested tasks for today ({len(tasks)}):"]
    for i, t in enumerate(tasks, 1):
        line = f"  {i}. {t.title} | priority:{t.priority} | energy:{t.energy}"
        if t.due_at:
            if t.due_at < now:
                line += " [OVERDUE]"
            else:
                line += f" | due:{t.due_at.strftime('%Y-%m-%d %H:%M UTC')}"
        lines.append(line)
    return "\n".join(lines)


def handle_update_task(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    from trellis.tasks import AmbiguousTaskError, TaskNotFoundError, UNSET

    reference = input_dict["reference"]
    new_title = input_dict.get("new_title") or None

    due_at_sentinel = UNSET
    if "due_at" in input_dict:
        raw = input_dict["due_at"]
        if raw is None:
            due_at_sentinel = None
        else:
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                due_at_sentinel = parsed
            except ValueError:
                return f"Could not parse due date '{raw}'. Use ISO 8601 format."

    priority = input_dict.get("priority") or None
    energy = input_dict.get("energy") or None

    try:
        task = task_service.update_task(
            user_id, reference,
            new_title=new_title,
            due_at=due_at_sentinel,
            priority=priority,
            energy=energy,
        )
    except TaskNotFoundError:
        return f"No open task found matching '{reference}'."
    except AmbiguousTaskError as exc:
        titles = ", ".join(f"'{t.title}'" for t in exc.matches)
        return f"Multiple tasks matched '{reference}': {titles}. Be more specific."
    except ValueError as exc:
        return str(exc)

    parts = [f"Updated: '{task.title}'"]
    if task.due_at:
        parts.append(f"due {task.due_at.strftime('%Y-%m-%d %H:%M UTC')}")
    elif "due_at" in input_dict and input_dict["due_at"] is None:
        parts.append("due date cleared")
    if priority:
        parts.append(f"priority: {task.priority.value}")
    if energy:
        parts.append(f"energy: {task.energy.value}")
    return " | ".join(parts)


def handle_archive_task(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    from trellis.tasks import AmbiguousTaskError, TaskNotFoundError

    reference = input_dict["reference"]
    try:
        archived = task_service.archive(user_id, reference)
    except TaskNotFoundError:
        return f"No open task found matching '{reference}'."
    except AmbiguousTaskError as exc:
        titles = ", ".join(f"'{t.title}'" for t in exc.matches)
        return f"Multiple tasks matched '{reference}': {titles}. Be more specific."

    if len(archived) == 1:
        return f"Archived: '{archived[0].title}'"
    titles = ", ".join(f"'{t.title}'" for t in archived)
    return f"Archived {len(archived)} tasks: {titles}"


def handle_set_reminder(user_id: UUID, input_dict: dict, now: datetime, *, task_service, reminder_service) -> str:
    from trellis.reminders import ReminderSchedulingError

    label = str(input_dict.get("label", "")).strip()
    remind_at_raw = input_dict["remind_at"]

    if not label:
        return "A label is required."

    try:
        remind_at = datetime.fromisoformat(remind_at_raw)
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return f"Could not parse reminder time '{remind_at_raw}'. Use ISO 8601 format."

    try:
        # Try to find an open task first
        tasks = task_service.list_open(user_id)
        query = label.lower()
        exact = [t for t in tasks if t.title.lower() == query]
        partial = [t for t in tasks if query in t.title.lower()]
        candidates = exact or partial

        if len(candidates) == 1:
            task = candidates[0]
            reminder = reminder_service.schedule_task_reminder(user_id, task, remind_at, now=now)
            return (
                f"Reminder set for '{task.title}' at "
                f"{reminder.remind_at.strftime('%Y-%m-%d %H:%M UTC')}"
            )
        if len(candidates) > 1 and not exact:
            titles = ", ".join(f"'{t.title}'" for t in candidates)
            return f"Multiple tasks matched '{label}': {titles}. Be more specific."

        # No task found — standalone reminder
        reminder = reminder_service.schedule_standalone_reminder(user_id, label, remind_at, now=now)
        return f"Reminder set: '{label}' at {reminder.remind_at.strftime('%Y-%m-%d %H:%M UTC')}"
    except ReminderSchedulingError as exc:
        return f"Could not set reminder: {exc}"


def handle_cancel_reminder(user_id: UUID, input_dict: dict, now: datetime, *, reminder_service) -> str:
    reference = str(input_dict.get("reference", "")).strip()
    if not reference:
        return "A reference is required."

    reminders = reminder_service.list_scheduled(user_id)
    if not reminders:
        return "No scheduled reminders."

    query = reference.lower()
    exact = [r for r in reminders if r.task_title.lower() == query]
    partial = [r for r in reminders if query in r.task_title.lower()]
    candidates = exact or partial

    if not candidates:
        return f"No scheduled reminder found matching '{reference}'."
    if len(candidates) > 1 and not exact:
        labels = ", ".join(f"'{r.task_title}'" for r in candidates)
        return f"Multiple reminders matched '{reference}': {labels}. Be more specific."

    target = candidates[0]
    try:
        reminder_service.cancel(target.id)
    except Exception:
        return "Couldn't cancel that reminder — try again."
    return f"Reminder cancelled: '{target.task_title}'"


def handle_list_reminders(user_id: UUID, input_dict: dict, now: datetime, *, reminder_service) -> str:
    reminders = reminder_service.list_scheduled(user_id)
    if not reminders:
        return "No scheduled reminders."

    lines = [f"Scheduled reminders ({len(reminders)}):"]
    for r in reminders:
        time_str = r.remind_at.strftime("%Y-%m-%d %H:%M UTC")
        if r.remind_at < now:
            time_str += " [OVERDUE]"
        lines.append(f"  {r.task_title} @ {time_str}")
    return "\n".join(lines)


def handle_list_completed_tasks(user_id: UUID, input_dict: dict, now: datetime, *, task_service) -> str:
    tasks = task_service.list_completed(user_id, limit=20)
    if not tasks:
        return "No completed tasks yet."

    lines = [f"Recently completed ({len(tasks)}):"]
    for t in tasks:
        line = f"  ✓ {t.title}"
        if t.completed_at:
            line += f" — {t.completed_at.strftime('%d %b')}"
        lines.append(line)
    return "\n".join(lines)


def handle_respond_to_insight(
    user_id: UUID, input_dict: dict, now: datetime, *, insight_repository,
) -> str:
    insight_summary = str(input_dict.get("insight_summary", "")).strip()
    action = str(input_dict.get("action", "")).strip()
    note = input_dict.get("note") or None

    if not insight_summary or action not in ("snooze", "resolve", "reject"):
        return "insight_summary and a valid action (snooze/resolve/reject) are required."

    insights = insight_repository.list_active(user_id)
    query = insight_summary.lower()
    matches = [i for i in insights if query in i.summary.lower()]

    if not matches:
        return f"No active insight found matching '{insight_summary}'."
    if len(matches) > 1:
        summaries = "; ".join(i.summary[:60] for i in matches)
        return f"Multiple insights matched — be more specific: {summaries}"

    insight = matches[0]
    insight_repository.respond(insight.id, action, note, now.date())

    if action == "snooze":
        return f"Insight snoozed for 7 days: '{insight.summary[:80]}'"
    elif action == "resolve":
        return f"Insight resolved: '{insight.summary[:80]}'"
    else:
        return f"Insight rejected (false positive): '{insight.summary[:80]}'"


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

EF_SIGNALS = [
    "task", "tasks", "todo", "todos", "to-do", "to-dos", "to do", "to dos",
    "remind", "reminder", "reminders", "alarm", "alarms",
    "do today", "later", "schedule", "plan", "morning", "evening",
    "before", "after", "priority", "urgent", "overdue",
    "complete", "done", "tick off", "cross off",
    "add task", "create task", "list tasks", "new task",
    "what do I need to", "checklist", "agenda", "deadline",
    "follow up", "don't forget", "remember to", "note to self",
    "next", "inbox", "triage",
    "executive function", "adhd", "upcoming", "backlog",
    "what's on", "what have I got", "what do I have",
]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def ef_tools(task_service, reminder_service, insight_repository=None) -> list[tuple[dict, callable]]:
    tools = [
        (CREATE_TASK_TOOL,
         lambda uid, inp, now: handle_create_task(uid, inp, now, task_service=task_service)),
        (COMPLETE_TASK_TOOL,
         lambda uid, inp, now: handle_complete_task(uid, inp, now, task_service=task_service)),
        (LIST_TASKS_TOOL,
         lambda uid, inp, now: handle_list_tasks(uid, inp, now, task_service=task_service)),
        (SELECT_TODAY_TASKS_TOOL,
         lambda uid, inp, now: handle_select_today_tasks(uid, inp, now, task_service=task_service)),
        (UPDATE_TASK_TOOL,
         lambda uid, inp, now: handle_update_task(uid, inp, now, task_service=task_service)),
        (ARCHIVE_TASK_TOOL,
         lambda uid, inp, now: handle_archive_task(uid, inp, now, task_service=task_service)),
        (LIST_COMPLETED_TASKS_TOOL,
         lambda uid, inp, now: handle_list_completed_tasks(uid, inp, now, task_service=task_service)),
        (SET_REMINDER_TOOL,
         lambda uid, inp, now: handle_set_reminder(
             uid, inp, now, task_service=task_service, reminder_service=reminder_service
         )),
        (CANCEL_REMINDER_TOOL,
         lambda uid, inp, now: handle_cancel_reminder(uid, inp, now, reminder_service=reminder_service)),
        (LIST_REMINDERS_TOOL,
         lambda uid, inp, now: handle_list_reminders(uid, inp, now, reminder_service=reminder_service)),
    ]
    if insight_repository is not None:
        tools.append((
            RESPOND_TO_INSIGHT_TOOL,
            lambda uid, inp, now: handle_respond_to_insight(
                uid, inp, now, insight_repository=insight_repository,
            ),
        ))
    return tools
