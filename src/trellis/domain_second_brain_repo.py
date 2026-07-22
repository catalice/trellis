from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.domain_second_brain_models import (
    Capture,
    CaptureType,
    Effort,
    EffortIntensity,
    Goal,
    GoalStatus,
    GoalType,
    Reminder,
    StateLog,
    Task,
    TaskEnergy,
    TaskEvent,
    TaskPriority,
    TaskStatus,
    TrackingEvent,
    TrackingEventType,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CaptureRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresCaptureRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save(self, capture: Capture) -> Capture:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO captures (
                        id, user_id, raw, capture_type, synthesis, summary,
                        effort_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        capture.id,
                        capture.user_id,
                        capture.raw,
                        str(capture.capture_type),
                        capture.synthesis,
                        capture.summary,
                        capture.effort_id,
                        capture.created_at,
                    ),
                )
        return capture

    def get(self, capture_id: UUID) -> Capture | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM captures WHERE id = %s",
                    (capture_id,),
                )
                row = cur.fetchone()
                return _capture(row) if row else None

    def list_recent(self, user_id: UUID, *, limit: int) -> list[Capture]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM captures
                    WHERE user_id = %s AND archived = false
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [_capture(r) for r in cur.fetchall()]

    def list_unassigned(self, user_id: UUID, *, since: date) -> list[Capture]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM captures
                    WHERE user_id = %s
                      AND effort_id IS NULL
                      AND archived = false
                      AND created_at::date >= %s
                    ORDER BY created_at ASC
                    """,
                    (user_id, since),
                )
                return [_capture(r) for r in cur.fetchall()]

    def assign_to_effort(self, capture_id: UUID, effort_id: UUID | None) -> Capture:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE captures SET effort_id = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (effort_id, capture_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(capture_id)
                return _capture(row)

    def archive(self, capture_id: UUID) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE captures SET archived = true WHERE id = %s",
                    (capture_id,),
                )


# ---------------------------------------------------------------------------
# EffortRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresEffortRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save(self, effort: Effort) -> Effort:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO efforts (
                        id, user_id, title, intensity, notes,
                        obsidian_path, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        effort.id,
                        effort.user_id,
                        effort.title,
                        str(effort.intensity),
                        effort.notes,
                        effort.obsidian_path,
                        effort.created_at,
                        effort.updated_at,
                    ),
                )
        return effort

    def get(self, effort_id: UUID) -> Effort | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM efforts WHERE id = %s", (effort_id,))
                row = cur.fetchone()
                return _effort(row) if row else None

    def list_all(self, user_id: UUID) -> list[Effort]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM efforts
                    WHERE user_id = %s
                    ORDER BY
                        CASE intensity
                            WHEN 'active' THEN 1
                            WHEN 'simmering' THEN 2
                            WHEN 'dormant' THEN 3
                            WHEN 'future' THEN 4
                        END, title
                    """,
                    (user_id,),
                )
                return [_effort(r) for r in cur.fetchall()]

    def update_intensity(self, effort_id: UUID, intensity: EffortIntensity) -> Effort:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE efforts SET intensity = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (str(intensity), effort_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(effort_id)
                return _effort(row)

    def update_notes(self, effort_id: UUID, notes: str) -> Effort:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE efforts SET notes = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (notes, effort_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(effort_id)
                return _effort(row)


# ---------------------------------------------------------------------------
# TaskRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresTaskRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save(self, task: Task) -> Task:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (
                        id, user_id, title, status, priority, energy,
                        description, due_at, source_capture_id,
                        created_at, updated_at, completed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task.id, task.user_id, task.title,
                        str(task.status), str(task.priority), str(task.energy),
                        task.description, task.due_at, task.source_capture_id,
                        task.created_at, task.updated_at, task.completed_at,
                    ),
                )
        return task

    def get(self, task_id: UUID) -> Task | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                return _task(row) if row else None

    def list_open(self, user_id: UUID) -> list[Task]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s AND status IN ('open', 'in_progress')
                    ORDER BY due_at NULLS LAST, priority DESC, created_at
                    """,
                    (user_id,),
                )
                return [_task(r) for r in cur.fetchall()]

    def list_recent(self, user_id: UUID, *, limit: int) -> list[Task]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [_task(r) for r in cur.fetchall()]

    def update(self, task_id: UUID, **kwargs: Any) -> Task:
        if not kwargs:
            raise ValueError("update called with no fields")
        allowed = {
            "title", "status", "priority", "energy",
            "description", "due_at", "updated_at", "completed_at",
        }
        unknown = set(kwargs) - allowed
        if unknown:
            raise ValueError(f"unknown task fields: {unknown}")
        # Serialize enum values
        serialised = {
            k: str(v) if hasattr(v, "value") else v
            for k, v in kwargs.items()
        }
        cols = ", ".join(f"{k} = %s" for k in serialised)
        vals = list(serialised.values()) + [task_id]
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE tasks SET {cols} WHERE id = %s RETURNING *",
                    vals,
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(task_id)
                return _task(row)

    def save_event(self, event: TaskEvent) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO task_events (
                        id, task_id, user_id, event_type, reason, occurred_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.id, event.task_id, event.user_id,
                        event.event_type, event.reason, event.occurred_at,
                    ),
                )


# ---------------------------------------------------------------------------
# ReminderRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresReminderRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save(self, reminder: Reminder) -> Reminder:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reminders (
                        id, user_id, label, remind_at, status,
                        task_id, recur_daily, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reminder.id, reminder.user_id, reminder.label,
                        reminder.remind_at, reminder.status,
                        reminder.task_id, reminder.recur_daily,
                        reminder.created_at,
                    ),
                )
        return reminder

    def get(self, reminder_id: UUID) -> Reminder | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
                row = cur.fetchone()
                return _reminder(row) if row else None

    def list_upcoming(self, user_id: UUID, *, before: datetime) -> list[Reminder]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM reminders
                    WHERE user_id = %s AND status = 'scheduled' AND remind_at <= %s
                    ORDER BY remind_at
                    """,
                    (user_id, before),
                )
                return [_reminder(r) for r in cur.fetchall()]

    def list_recent(self, user_id: UUID, *, limit: int) -> list[Reminder]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM reminders
                    WHERE user_id = %s
                    ORDER BY remind_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [_reminder(r) for r in cur.fetchall()]

    def cancel(self, reminder_id: UUID) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reminders SET status = 'cancelled' WHERE id = %s",
                    (reminder_id,),
                )

    def mark_sent(self, reminder_id: UUID) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reminders SET status = 'sent' WHERE id = %s",
                    (reminder_id,),
                )


# ---------------------------------------------------------------------------
# GoalRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresGoalRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save(self, goal: Goal) -> Goal:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO goals (
                        id, user_id, title, goal_type, status,
                        target_date, is_fixed_date, notes,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        goal.id, goal.user_id, goal.title,
                        str(goal.goal_type), str(goal.status),
                        goal.target_date, goal.is_fixed_date, goal.notes,
                        goal.created_at, goal.updated_at,
                    ),
                )
        return goal

    def get(self, goal_id: UUID) -> Goal | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM goals WHERE id = %s", (goal_id,))
                row = cur.fetchone()
                return _goal(row) if row else None

    def list_active(self, user_id: UUID) -> list[Goal]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM goals
                    WHERE user_id = %s AND status = 'active'
                    ORDER BY
                        CASE goal_type
                            WHEN 'race' THEN 1
                            WHEN 'aerobic' THEN 2
                            WHEN 'strength' THEN 3
                            ELSE 4
                        END, target_date NULLS LAST, created_at
                    """,
                    (user_id,),
                )
                return [_goal(r) for r in cur.fetchall()]

    def update(self, goal_id: UUID, **kwargs: Any) -> Goal:
        if not kwargs:
            raise ValueError("update called with no fields")
        allowed = {"title", "goal_type", "status", "target_date", "is_fixed_date", "notes", "updated_at"}
        unknown = set(kwargs) - allowed
        if unknown:
            raise ValueError(f"unknown goal fields: {unknown}")
        serialised = {
            k: str(v) if hasattr(v, "value") else v
            for k, v in kwargs.items()
        }
        cols = ", ".join(f"{k} = %s" for k in serialised)
        vals = list(serialised.values()) + [goal_id]
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE goals SET {cols} WHERE id = %s RETURNING *",
                    vals,
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(goal_id)
                return _goal(row)


# ---------------------------------------------------------------------------
# StateRepository — Postgres
# ---------------------------------------------------------------------------

class PostgresStateRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save_state(self, log: StateLog) -> StateLog:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO state_logs (id, user_id, note, energy, mood, felt_at, logged_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (log.id, log.user_id, log.note, log.energy, log.mood, log.felt_at, log.logged_at),
                )
        return log

    def delete_state(self, user_id: UUID, log_id: UUID) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM state_logs WHERE id = %s AND user_id = %s",
                    (log_id, user_id),
                )
                return cur.rowcount > 0

    def delete_event(self, user_id: UUID, event_id: UUID) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tracking_events WHERE id = %s AND user_id = %s",
                    (event_id, user_id),
                )
                return cur.rowcount > 0

    def save_event(self, event: TrackingEvent) -> TrackingEvent:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tracking_events (id, user_id, event_type, detail, value, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.id, event.user_id, str(event.event_type),
                        event.detail, event.value, event.occurred_at,
                    ),
                )
        return event

    def list_states_since(self, user_id: UUID, *, since: datetime) -> list[StateLog]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM state_logs
                    WHERE user_id = %s AND felt_at >= %s
                    ORDER BY felt_at
                    """,
                    (user_id, since),
                )
                return [_state_log(r) for r in cur.fetchall()]

    def list_events_since(self, user_id: UUID, *, since: datetime) -> list[TrackingEvent]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_events
                    WHERE user_id = %s AND occurred_at >= %s
                    ORDER BY occurred_at
                    """,
                    (user_id, since),
                )
                return [_tracking_event(r) for r in cur.fetchall()]

    def last_period_start(self, user_id: UUID) -> TrackingEvent | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_events
                    WHERE user_id = %s AND event_type = 'period_start'
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                return _tracking_event(row) if row else None


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------

def _state_log(row: dict) -> StateLog:
    return StateLog(
        id=row["id"],
        user_id=row["user_id"],
        note=row["note"],
        energy=row.get("energy"),
        mood=row.get("mood"),
        felt_at=row["felt_at"],
        logged_at=row["logged_at"],
    )


def _tracking_event(row: dict) -> TrackingEvent:
    return TrackingEvent(
        id=row["id"],
        user_id=row["user_id"],
        event_type=TrackingEventType(row["event_type"]),
        detail=row.get("detail"),
        value=float(row["value"]) if row.get("value") is not None else None,
        occurred_at=row["occurred_at"],
    )


def _capture(row: dict) -> Capture:
    return Capture(
        id=row["id"],
        user_id=row["user_id"],
        raw=row["raw"],
        capture_type=CaptureType(row["capture_type"]),
        synthesis=row.get("synthesis"),
        summary=row.get("summary"),
        effort_id=row.get("effort_id"),
        created_at=row["created_at"],
    )


def _effort(row: dict) -> Effort:
    return Effort(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        intensity=EffortIntensity(row["intensity"]),
        notes=row.get("notes"),
        obsidian_path=row.get("obsidian_path"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _task(row: dict) -> Task:
    return Task(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        status=TaskStatus(row["status"]),
        priority=TaskPriority(row["priority"]),
        energy=TaskEnergy(row["energy"]),
        description=row.get("description"),
        due_at=row.get("due_at"),
        source_capture_id=row.get("source_capture_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row.get("completed_at"),
    )


def _reminder(row: dict) -> Reminder:
    return Reminder(
        id=row["id"],
        user_id=row["user_id"],
        label=row["label"],
        remind_at=row["remind_at"],
        status=row["status"],
        task_id=row.get("task_id"),
        recur_daily=row.get("recur_daily", False),
        created_at=row["created_at"],
    )


def _goal(row: dict) -> Goal:
    return Goal(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        goal_type=GoalType(row["goal_type"]),
        status=GoalStatus(row["status"]),
        target_date=row.get("target_date"),
        is_fixed_date=row.get("is_fixed_date", False),
        notes=row.get("notes"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
