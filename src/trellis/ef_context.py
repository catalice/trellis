"""
Context loader for the executive function (EF) domain.

Assembles the EF-relevant section of the system prompt: overdue tasks,
tasks due today, remaining open tasks grouped by priority, and reminders
due in the next 4 hours.

Usage in main.py:
    registry.add_domain("ef", ef_context_loader(...), ...)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID

from trellis.registry import ContextLoader

_log = logging.getLogger(__name__)

_MAX_TASKS = 20


# --- Protocols (structural — no imports from service files) -----------------

class _TaskService(Protocol):
    def list_open(self, user_id: UUID) -> list: ...


class _ReminderService(Protocol):
    def due_between(self, user_id: UUID, start_at: datetime, end_at: datetime) -> list: ...


# --- Factory ----------------------------------------------------------------

def ef_context_loader(
    task_service: _TaskService,
    reminder_service: _ReminderService,
) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = []

        tasks_shown = 0
        try:
            tasks = task_service.list_open(user_id)
            overdue = [t for t in tasks if t.due_at and t.due_at < now]
            due_today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            due_today_end = due_today_start + timedelta(days=1)
            due_today = [
                t for t in tasks
                if t.due_at
                and due_today_start <= t.due_at < due_today_end
                and t not in overdue
            ]
            shown_ids = {t.id for t in overdue} | {t.id for t in due_today}
            remaining = [t for t in tasks if t.id not in shown_ids]

            if overdue:
                lines = ["Overdue:"]
                for t in overdue:
                    if tasks_shown >= _MAX_TASKS:
                        break
                    lines.append("  " + _format_task(t) + " [OVERDUE]")
                    tasks_shown += 1
                parts.append("\n".join(lines))

            if due_today and tasks_shown < _MAX_TASKS:
                lines = ["Due today:"]
                for t in due_today:
                    if tasks_shown >= _MAX_TASKS:
                        break
                    lines.append("  " + _format_task(t) + " [DUE TODAY]")
                    tasks_shown += 1
                parts.append("\n".join(lines))

            if remaining and tasks_shown < _MAX_TASKS:
                by_priority: dict[str, list] = {"high": [], "medium": [], "low": []}
                for t in remaining:
                    bucket = by_priority.get(t.priority, by_priority["low"])
                    bucket.append(t)

                lines = ["Open tasks:"]
                for level in ("high", "medium", "low"):
                    for t in by_priority[level]:
                        if tasks_shown >= _MAX_TASKS:
                            break
                        lines.append("  " + _format_task(t))
                        tasks_shown += 1
                if len(lines) > 1:
                    parts.append("\n".join(lines))
        except Exception:
            _log.warning("ef_context: tasks load failed", exc_info=True)

        try:
            window_end = now + timedelta(hours=4)
            upcoming = reminder_service.due_between(user_id, now, window_end)
            if upcoming:
                lines = ["Reminders due in 4h:"]
                for r in upcoming:
                    time_str = r.remind_at.astimezone(timezone.utc).strftime("%H:%M UTC")
                    lines.append(f"  {r.task_title} @ {time_str}")
                parts.append("\n".join(lines))
        except Exception:
            _log.warning("ef_context: reminders load failed", exc_info=True)

        if not parts:
            return None
        return "[Executive Function]\n" + "\n\n".join(parts)

    return loader


# --- Formatting helpers -----------------------------------------------------

def _format_task(task) -> str:
    parts = [task.title, f"priority:{task.priority}", f"energy:{task.energy}"]
    if task.due_at:
        parts.append(f"due:{task.due_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return " | ".join(parts)
