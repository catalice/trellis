"""
Tool schemas and handlers for the focus domain.

Handler signature: (user_id, input_dict, now) -> str
Context loaders: focus_context_loader, focus_snapshot
Registration: focus_tools(...)
"""
from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_focus_models import (
    GoalStatus,
    TaskEnergy,
    TaskPriority,
)
from trellis.core_actions import done, failed, partial, refused
from trellis.domain_focus_service import GoalNotFoundError, PageTaken, TaskNotFoundError

_log = logging.getLogger(__name__)

# (user_id, now) -> context string or None
ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas — Claude tools API format
# ---------------------------------------------------------------------------

FOCUS_ADD_TOOL: dict = {
    "name": "focus_add",
    "description": (
        "Create one Focus record: a task, a goal, a reminder, or a note on an "
        "effort. Pick 'what', send only that entity's fields. A task only when "
        "they ask for one — a passing remark is not a request. Result warns about "
        "same-named duplicates — read it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["task", "goal", "reminder", "effort_note"],
                "description": (
                    "task: todo or seed. goal. reminder: their words posted back "
                    "at a time — or a check-in Trellis runs itself (check_in). "
                    "effort_note: content kept on an effort page, or an existing "
                    "capture filed there (capture_id)."
                ),
            },
            "title": {"type": "string", "description": "task/goal: what it is. Required."},
            "kind": {
                "type": "string", "enum": ["todo", "seed"], "default": "todo",
                "description": "task: todo = owed. seed = curiosity — no due date, never nags.",
            },
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "task: default medium."},
            "energy": {
                "type": "string", "enum": ["low", "medium", "high"],
                "description": "task: low = routine, high = full focus. Default medium.",
            },
            "description": {"type": "string", "description": "task: optional detail."},
            "due": {
                "type": "string",
                "description": (
                    "task: user-local YYYY-MM-DDTHH:MM or YYYY-MM-DD, as they mean "
                    "it — no timezone conversion. Omit if no deadline."
                ),
            },
            "target_date": {"type": "string", "description": "goal: YYYY-MM-DD. Omit if open-ended."},
            "is_fixed_date": {"type": "boolean", "description": "goal: true if the date cannot move."},
            "notes": {"type": "string", "description": "goal: optional notes."},
            "label": {
                "type": "string",
                "description": (
                    "reminder: what it's for — required. "
                    "goal: optional tag, only if they name one; reuse their "
                    "existing labels. 'race'/'aerobic'/'strength' reach the coach."
                ),
            },
            "remind_at": {
                "type": "string",
                "description": (
                    "reminder: user-local YYYY-MM-DDTHH:MM, exactly the time they "
                    "said. Required."
                ),
            },
            "task_id": {"type": "string", "description": "reminder: link to an existing task, if any."},
            "check_in": {
                "type": "boolean",
                "default": False,
                "description": (
                    "reminder: true = Trellis wakes at that time and runs a turn "
                    "with the label as ITS instruction, speaking first. Anything "
                    "phrased as something Trellis should DO then is a check-in; "
                    "'remind me to…' is not."
                ),
            },
            "recurrence": {
                "type": "string", "enum": ["daily", "weekly", "monthly", "yearly"],
                "description": "reminder: how it repeats; remind_at is the first firing. Omit for a one-off.",
            },
            "effort_title": {
                "type": "string",
                "description": "effort_note: the effort's name. Same exact name = same page. Required.",
            },
            "content": {"type": "string", "description": "effort_note: what to keep — full, links and all."},
            "graduated_seed_id": {"type": "string", "description": "effort_note: the seed this grew from — it gets retired."},
            "capture_id": {"type": "string", "description": "effort_note: an inbox capture to file here instead of content."},
        },
        "required": ["what"],
        "additionalProperties": False,
    },
}

FOCUS_UPDATE_TOOL: dict = {
    "name": "focus_update",
    "description": (
        "Change one existing Focus record by id; send only what changes. "
        "Reminder: cancelled is the only change — to move one, cancel it and "
        "add anew. Effort: title only (its page moves with it)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["task", "goal", "reminder", "effort"]},
            "id": {"type": "string", "description": "UUID of the record (from focus_get)."},
            "title": {"type": "string", "description": "task/goal/effort: new title."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "task."},
            "energy": {"type": "string", "enum": ["low", "medium", "high"], "description": "task."},
            "kind": {"type": "string", "enum": ["todo", "seed"], "description": "task: reclassify."},
            "status": {
                "type": "string",
                "enum": ["open", "done", "dropped", "parked", "active", "achieved", "paused", "cancelled"],
                "description": (
                    "task: open/done/dropped/parked — dropped is gone for good, "
                    "parked is shelved. goal: active/achieved/paused/dropped. "
                    "reminder: cancelled."
                ),
            },
            "due": {"type": "string", "description": "task: user-local YYYY-MM-DDTHH:MM or YYYY-MM-DD."},
            "description": {"type": "string", "description": "task."},
            "target_date": {"type": "string", "description": "goal: YYYY-MM-DD."},
            "is_fixed_date": {"type": "boolean", "description": "goal."},
            "notes": {"type": "string", "description": "goal: REPLACES stored notes — the result echoes what was overwritten."},
            "label": {"type": "string", "description": "goal: change its tag; empty string clears it."},
        },
        "required": ["what", "id"],
        "additionalProperties": False,
    },
}

BRAIN_DUMP_TOOL: dict = {
    "name": "brain_dump",
    "description": (
        "Capture what they want out of their head — ideas, tasks, questions, "
        "half-formed thoughts — in their exact words. The raw text is kept whole; "
        "synthesis sits beside it and extracts tasks. Result lists what was "
        "created and what was skipped as a duplicate — read it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Exactly what they said. Don't clean it up.",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}

FOCUS_GET_TOOL: dict = {
    "name": "focus_get",
    "description": (
        "Read the Focus stores. Call before presenting tasks, goals, inbox, "
        "efforts or reminders — never from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["tasks", "seeds", "goals", "inbox", "efforts", "effort", "reminders"],
                "description": (
                    "tasks: open todos by urgency, plus parked. "
                    "seeds: the exploration menu. "
                    "goals: active goals. "
                    "inbox: captures not yet homed. "
                    "efforts: all, by intensity. "
                    "effort: one effort's full page with note ids (pass name) — "
                    "read it before advising on a project. "
                    "reminders: everything scheduled, with ids and recurrence, plus recent deliveries."
                ),
            },
            "name": {"type": "string", "description": "effort: the effort's title."},
        },
        "required": ["what"],
    },
}

# (The per-entity write schemas were folded into FOCUS_ADD/FOCUS_UPDATE — the
# handlers survive behind the dispatch. ADD_GOAL_TOOL keeps a schema for
# onboarding only; it is not in the main registry.)

ADD_GOAL_TOOL: dict = {
    "name": "add_goal",
    "description": "Save a goal they name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "label": {
                "type": "string",
                "description": (
                    "Optional tag, only if they name one. "
                    "'race'/'aerobic'/'strength' reach the coach."
                ),
            },
            "target_date": {
                "type": "string",
                "description": "YYYY-MM-DD. Omit if open-ended.",
            },
            "is_fixed_date": {
                "type": "boolean",
                "description": "true if the date cannot move.",
                "default": False,
            },
            "notes": {"type": "string"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

DELETE_ENTRY_TOOL: dict = {
    "name": "delete_entry",
    "description": (
        "Erase a record that should never have existed — a duplicate, a wrong "
        "tracking entry, a mis-capture. Gone completely. A decision is not a "
        "mistake: a task they decided against is focus_update status='dropped'. "
        "Corrections are erase + re-log. Erasing a capture leaves the tasks "
        "extracted from it; erasing a task takes its reminders with it; an "
        "effort erases only while empty. Ids come from focus_get."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "UUID of the record to erase."},
        },
        "required": ["entry_id"],
        "additionalProperties": False,
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
        return refused("No text provided — nothing to capture.")

    result = brain_dump_service.process(user_id, raw, now)

    if result.synthesis is None:
        return partial(
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

    if result.duplicates_skipped:
        parts.append(
            "\nAlready on the open list (not re-created): "
            + ", ".join(result.duplicates_skipped)
        )

    if syn.questions:
        parts.append("\nOpen questions:\n" + "\n".join(f"  ? {q}" for q in syn.questions))

    if syn.effort_hints:
        parts.append(
            "\nThis might be worth naming an Effort: "
            + ", ".join(syn.effort_hints)
        )

    return done("\n".join(parts))


@dataclass(frozen=True)
class _FocusReads:
    """Everything a read-view formatter may need — ONE shape for all views,
    so the dispatch table stays a table and formatters stay swappable
    (branch inputs aren't uniform: 'effort' needs a name, views use
    different service subsets — the shared context absorbs that)."""
    task_service: Any
    goal_service: Any
    capture_service: Any
    effort_service: Any
    reminder_service: Any
    tz: Any


def _view_tasks(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    tasks = ctx.task_service.list_open(user_id)
    parked = ctx.task_service.list_parked(user_id)
    if not tasks and not parked:
        return "No open tasks."
    overdue = [t for t in tasks if t.is_overdue(now)]
    rest = [t for t in tasks if not t.is_overdue(now)]
    lines = []
    if overdue:
        lines.append("OVERDUE:")
        for t in overdue:
            lines.append(f"  [{t.id}] {t.title} — due {_fmt_datetime(t.due_at, ctx.tz)} | {t.priority}/{t.energy}")
    if rest:
        lines.append("Open:")
        for t in rest:
            due = f" — due {_fmt_datetime(t.due_at, ctx.tz)}" if t.due_at else ""
            lines.append(f"  [{t.id}] {t.title}{due} | {t.priority}/{t.energy}")
    if parked:
        lines.append("Parked (not now, on the shelf):")
        for t in parked:
            lines.append(f"  [{t.id}] {t.title}")
    return "\n".join(lines)


def _view_seeds(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    seeds = ctx.task_service.list_seeds(user_id)
    if not seeds:
        return "No seeds planted yet."
    lines = ["Seeds (no obligation, pick what sparks):"]
    for t in seeds:
        lines.append(f"  [{t.id}] {t.title} | energy {t.energy}")
    return "\n".join(lines)


def _view_goals(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    goals = ctx.goal_service.list_active(user_id)
    if not goals:
        return "No active goals."
    return "\n".join(f"  [{g.id}] {g.summary()}" for g in goals)


def _view_inbox(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    captures = ctx.capture_service.list_unassigned(user_id)
    if not captures:
        return "Inbox is clear — no unassigned captures."
    lines = [f"Unassigned captures ({len(captures)}):"]
    for c in captures:
        date_str = c.created_at.astimezone(ctx.tz).strftime("%d %b")
        summary = c.summary or c.raw[:60]
        lines.append(f"  [{c.id}] {date_str} — {summary}")
    return "\n".join(lines)


def _view_efforts(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    efforts = ctx.effort_service.list_all(user_id)
    if not efforts:
        return "No efforts yet. They grow from saved research and graduated seeds."
    lines = []
    for e in efforts:
        lines.append(f"  [{e.id}] {e.title} ({e.intensity.value})")
    return "\n".join(lines)


def _view_effort(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    title = str(input_dict.get("name", "")).strip()
    if not title:
        return "name is required for what='effort'."
    page = ctx.effort_service.page(user_id, title, ctx.capture_service)
    if page is None:
        return f"No effort called '{title}'. focus_get what='efforts' lists them."
    effort, captures = page
    lines = [f"Effort: {effort.title} ({effort.intensity.value}) [{effort.id}]"]
    if effort.notes:
        lines.append(effort.notes)
    if not captures:
        lines.append("Nothing filed on it yet.")
    for c in captures:
        when = c.created_at.astimezone(ctx.tz).strftime("%d %b")
        body = (c.synthesis or c.raw or "").strip()
        lines.append(f"  [{c.id}] {when} — {body[:300]}")
    return "\n".join(lines)


def _view_reminders(user_id: UUID, input_dict: dict, now: datetime, ctx: _FocusReads) -> str:
    upcoming = ctx.reminder_service.all_scheduled(user_id)
    recent = [r for r in ctx.reminder_service.recent(user_id, limit=10) if r.status != "scheduled"]
    lines = []
    if upcoming:
        lines.append("Scheduled:")
        lines.extend(
            f"  [{r.id}] {'check-in: ' if r.kind == 'check_in' else ''}{r.label} @ {_fmt_datetime(r.remind_at, ctx.tz)}"
            + (f" (repeats {r.recurrence})" if r.recurrence else "")
            for r in upcoming
        )
    if recent:
        lines.append("Recent (delivery status):")
        lines.extend(
            f"  {'check-in: ' if r.kind == 'check_in' else ''}{r.label} @ {_fmt_datetime(r.remind_at, ctx.tz)} — {r.status}"
            for r in recent
        )
    return "\n".join(lines) if lines else "No reminders scheduled and none recently fired."


# The read views, one formatter per option. The unknown-option message derives
# from these keys, so it can never drift from what actually exists.
_GET_VIEWS: dict[str, Callable[[UUID, dict, datetime, _FocusReads], str]] = {
    "tasks": _view_tasks,
    "seeds": _view_seeds,
    "goals": _view_goals,
    "inbox": _view_inbox,
    "efforts": _view_efforts,
    "effort": _view_effort,
    "reminders": _view_reminders,
}


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
    ctx = _FocusReads(
        task_service=task_service,
        goal_service=goal_service,
        capture_service=capture_service,
        effort_service=effort_service,
        reminder_service=reminder_service,
        tz=tz,
    )
    what = str(input_dict.get("what", ""))
    view = _GET_VIEWS.get(what)
    if view is None:
        return f"Unknown option: {what!r}. Use: " + ", ".join(_GET_VIEWS) + "."
    return view(user_id, input_dict, now, ctx)


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
        return refused("Task title is required.")
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
        # Todos AND seeds — a duplicate seed is just as silently wasteful.
        existing = task_service.list_open(user_id) + task_service.list_seeds(user_id)
        dup = next(
            (t for t in existing
             if t.id != task.id and t.title.strip().lower() == title.lower()),
            None,
        )
        if dup is not None:
            result += (
                f"\nHeads up: an open item with this title already existed [{dup.id}]. "
                "If that makes this a duplicate, ask them which to drop."
            )
    except Exception:
        # Best-effort by design, but never mute: a persistently failing dup
        # check would otherwise vanish without trace.
        _log.debug("duplicate check failed", exc_info=True)
    return done(result)


def handle_update_task(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    task_service,
) -> str:
    task_id_str = str(input_dict.get("task_id", "")).strip()
    if not task_id_str:
        return refused("task_id is required.")
    try:
        task_id = UUID(task_id_str)
    except ValueError:
        return refused(f"Invalid task_id: {task_id_str!r}")

    # status='done' routes through complete(): it owns completed_at, the task
    # event, and the vault refresh — a plain status write would skip all three.
    completed = None
    if input_dict.get("status") == "done":
        try:
            completed = task_service.complete(user_id, task_id, now=now)
        except TaskNotFoundError:
            return refused(f"Task not found: {task_id_str}")

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
        elif input_dict["status"] != "done":
            # Refuse loudly — a silent drop here reported "Updated" while
            # changing nothing but updated_at.
            return refused(
                f"Invalid task status: {input_dict['status']!r}. "
                "Use open, done, dropped, or parked."
            )

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
        return done(f"Done: {completed.title}")

    try:
        task = task_service.update(user_id, task_id, **kwargs, now=now)
    except TaskNotFoundError:
        return refused(f"Task not found: {task_id_str}")
    if completed is not None:
        return done(f"Done + updated: {task.title}")
    return done(f"Updated: {task.title}")


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
        return refused("label and remind_at are both required.")
    try:
        remind_at = datetime.fromisoformat(remind_at_str.replace("Z", "+00:00"))
    except ValueError:
        return refused(f"Invalid remind_at format: {remind_at_str!r}. Use YYYY-MM-DDTHH:MM.")
    # Timezone math is Python's job, never Claude's: a naive datetime is the
    # user's local wall-clock time.
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=tz)
    if remind_at <= now:
        return refused(
            f"That time ({_fmt_datetime(remind_at, tz)}) is already past — a "
            "reminder set there would never usefully fire. Pick a future time."
        )

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
    kind = "check_in" if input_dict.get("check_in") else "remind"
    dup = None
    try:
        dup = next(
            (r for r in reminder_service.all_scheduled(user_id)
             if r.label.strip().lower() == label.lower() and r.kind == kind),
            None,
        )
    except Exception:
        dup = None
    reminder = reminder_service.set(user_id, label, remind_at, task_id=task_id,
                                    recurrence=recurrence, kind=kind, now=now)
    repeats = f", repeats {reminder.recurrence}" if reminder.recurrence else ""
    noun = "Check-in" if reminder.kind == "check_in" else "Reminder"
    result = f"{noun} set: {reminder.label} @ {_fmt_datetime(reminder.remind_at, tz)}{repeats} [{reminder.id}]"
    if dup is not None:
        dup_repeats = f", repeats {dup.recurrence}" if dup.recurrence else ""
        result += (
            f"\nHeads up: a scheduled {'check-in' if kind == 'check_in' else 'reminder'} with this label already existed — "
            f"@ {_fmt_datetime(dup.remind_at, tz)}{dup_repeats} [{dup.id}]. "
            "If that makes this a duplicate, ask them which to cancel."
        )
    return done(result)


def handle_cancel_reminder(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    reminder_service,
) -> str:
    rid_str = str(input_dict.get("reminder_id", "")).strip()
    if not rid_str:
        return refused("reminder_id is required.")
    try:
        cancelled = reminder_service.cancel(UUID(rid_str))
    except ValueError:
        return refused(f"Invalid reminder_id: {rid_str!r}")
    if not cancelled:
        return refused("No scheduled reminder with that id — focus_get what='reminders' lists them.")
    return done("Reminder cancelled.")


def handle_add_goal(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    goal_service,
) -> str:
    title = str(input_dict.get("title", "")).strip()
    if not title:
        return refused("title is required.")
    label = str(input_dict.get("label", "") or "").strip() or None

    target_date = None
    if input_dict.get("target_date"):
        try:
            target_date = date.fromisoformat(str(input_dict["target_date"]))
        except ValueError:
            return refused(f"Invalid target_date: {input_dict['target_date']!r}. Use YYYY-MM-DD.")

    goal = goal_service.add(
        user_id, title,
        label=label,
        target_date=target_date,
        # Strict identity check: bool("false") is True — only a real JSON true counts.
        is_fixed_date=input_dict.get("is_fixed_date") is True,
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
        # Best-effort by design, but never mute: a persistently failing dup
        # check would otherwise vanish without trace.
        _log.debug("duplicate check failed", exc_info=True)
    return done(result)


def handle_update_goal(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    goal_service,
) -> str:
    goal_id_str = str(input_dict.get("goal_id", "")).strip()
    if not goal_id_str:
        return refused("goal_id is required.")
    try:
        goal_id = UUID(goal_id_str)
    except ValueError:
        return refused(f"Invalid goal_id: {goal_id_str!r}")

    kwargs: dict[str, Any] = {}
    if "title" in input_dict:
        kwargs["title"] = str(input_dict["title"]).strip()
    if "label" in input_dict:
        kwargs["label"] = str(input_dict["label"] or "").strip() or None
    if "target_date" in input_dict and input_dict["target_date"]:
        try:
            kwargs["target_date"] = date.fromisoformat(str(input_dict["target_date"]))
        except ValueError:
            return refused(f"Invalid target_date format.")
    if "is_fixed_date" in input_dict:
        kwargs["is_fixed_date"] = input_dict["is_fixed_date"] is True
    if "notes" in input_dict:
        kwargs["notes"] = input_dict["notes"]
    if "status" in input_dict:
        try:
            kwargs["status"] = GoalStatus(input_dict["status"])
        except ValueError:
            return refused(f"Unknown status: {input_dict['status']!r}.")

    # Notes are a wholesale text replace — echo what got overwritten so a bad
    # rewrite is visible in the result, not silently gone. Fetched by id so
    # paused/achieved goals get the echo too, not just active ones.
    old_notes = None
    if "notes" in kwargs:
        try:
            existing = goal_service.get(user_id, goal_id)
            old_notes = existing.notes if existing else None
        except Exception:
            old_notes = None

    try:
        goal = goal_service.update(user_id, goal_id, **kwargs, now=now)
    except GoalNotFoundError:
        return refused(f"Goal not found: {goal_id_str}")
    result = f"Goal updated: {goal.summary()}"
    if old_notes and kwargs.get("notes") != old_notes:
        result += f'\nNotes replaced — they used to say: "{old_notes}"'
    return done(result)


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
        return refused("entry_id is required.")
    try:
        entry_id = UUID(entry_id_str)
    except ValueError:
        return refused(f"Invalid entry_id: {entry_id_str!r}")
    if (
        sense_service.delete_entry(user_id, entry_id)
        or task_service.delete(user_id, entry_id)
        or capture_service.delete(user_id, entry_id)
    ):
        return done("Erased.")
    verdict = (effort_service.delete_if_empty(user_id, entry_id, capture_service)
               if effort_service is not None else "not_found")
    if verdict == "deleted":
        return done("Erased (empty effort, page removed).")
    if verdict == "deleted_page_kept":
        return partial("Erased the effort record. Its vault page has writing on it that "
                       "Trellis didn't put there, so the page was left where it is.")
    if verdict == "not_empty":
        return refused("That effort still has notes filed on it — move them first "
                      "(focus_add what='effort_note' with capture_id), then erase.")
    return refused("No record with that id.")


WEB_SEARCH_TOOL: dict = {
    "name": "web_search",
    "description": (
        "Search outside their brain. web: general. news: current events, newest "
        "first. pubmed: peer-reviewed medicine. scholar: scholarly work, every "
        "field. trials: registered clinical trials. Citation sources return "
        "real papers with links — keepers go to a Learn map as kind='source'. "
        "Read-only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for. Specific. pubmed takes topic terms, not sentences."},
            "source": {
                "type": "string", "enum": ["web", "news", "pubmed", "scholar", "trials"],
                "description": "Default web.",
            },
        },
        "required": ["query"],
    },
}

RECALL_TOOL: dict = {
    "name": "recall",
    "description": (
        "Search their own brain by meaning — captures, efforts and seeds that "
        "relate to a query without sharing its words. For 'have I thought about "
        "this before', or when a new idea might echo a thread they already hold. "
        "Memory, not the web."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The idea to find relatives of. A phrase beats a single word.",
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
    return refused(f"Unknown what: {what!r}. Use: task, goal, reminder, effort_note.")


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
            return refused("Reminders only cancel (status='cancelled'). To move one: cancel it, then focus_add a new one.")
        return handle_cancel_reminder(
            user_id, {"reminder_id": rec_id}, now, reminder_service=reminder_service,
        )
    if what == "effort":
        new_title = str(input_dict.get("title", "")).strip()
        if not new_title:
            return refused("title is required to rename an effort.")
        try:
            renamed = effort_service.rename(user_id, UUID(rec_id), new_title)
        except PageTaken as exc:
            return failed(f"Not renamed — the vault already has a page at {exc.path} "
                          "(another effort, or a note written by hand). Nothing changed; pick another title.")
        except ValueError:
            return refused(f"Invalid id: {rec_id!r}")
        if renamed is None:
            return refused("No effort with that id.")
        return done(f"Renamed to '{renamed.title}' — its vault page moved with it.")
    return refused(f"Unknown what: {what!r}. Use: task, goal, reminder, effort.")


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
        return refused("effort_title is required.")
    if not content and not capture_id_str:
        return refused("Pass content (new material) or capture_id (an existing capture to file).")

    # A typo must not silently spawn a second effort: say when one was created,
    # and flag a near-miss against the existing names so a slip is caught in
    # the result, not months later in the vault.
    existing_titles = []
    try:
        existing_titles = [e.title for e in effort_service.list_all(user_id)]
    except Exception:
        _log.warning("save_to_effort: effort list failed", exc_info=True)
    was_existing = any(t.lower() == title.lower() for t in existing_titles)
    effort = effort_service.find_or_create(user_id, title, now)
    created_note = ""
    if not was_existing:
        created_note = " (new effort created)"
        close = difflib.get_close_matches(
            title.lower(), [t.lower() for t in existing_titles], n=1, cutoff=0.75,
        )
        if close:
            original = next(t for t in existing_titles if t.lower() == close[0])
            created_note += (
                f" — heads up: this is close to the existing effort "
                f"'{original}'. If that was the target, ask them, then move the "
                "note and erase the new effort."
            )

    if capture_id_str:
        try:
            capture_service.assign(user_id, UUID(capture_id_str), effort.id)
        except ValueError:
            return refused(f"Invalid capture_id: {capture_id_str!r}")
        except LookupError:
            return refused(f"No capture with id {capture_id_str} — focus_get what='inbox' lists them.")
        return done(f"Filed that capture into '{effort.title}'{created_note}.")
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
            retired = " (seed retirement FAILED — the seed is still on the list)"
    return done(f"Saved to effort '{effort.title}'{created_note}{retired}.")


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
    goal_service=None,
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

        # Dated goals become COMPUTED countdowns — facts from data, never typed,
        # never stale (a dated goal can't quietly expire).
        if goal_service is not None:
            try:
                today = now.date()
                bits = []
                for g in goal_service.list_active(user_id):
                    if g.target_date is None:
                        continue
                    days = (g.target_date - today).days
                    if days < 0 or days > 400:
                        continue
                    w, d = divmod(days, 7)
                    span = f"{w}w{d}d" if w else f"{d}d"
                    bits.append(f"{g.title} in {span} ({g.target_date.strftime('%-d %b')})")
                if bits:
                    parts.append("Dated: " + "; ".join(bits))
            except Exception:
                _log.warning("focus_snapshot: countdowns failed", exc_info=True)

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
