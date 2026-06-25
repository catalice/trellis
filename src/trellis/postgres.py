from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from uuid import uuid4

import psycopg2
from psycopg2.extras import RealDictCursor, register_uuid

from trellis.captures import Capture, Idea, Interpretation
from trellis.corrections import CorrectionPlan, CorrectionResult
from trellis.cycle import CycleEvent
from trellis.goals import Goal
from trellis.learn_models import LearningEntry, LearningThread
from trellis.training_arc import ArcPhase, TrainingArc
from trellis.reminders import ReminderIntent, ReminderStatus
from trellis.session_completion import SessionCompletion, WorkoutCheckin
from trellis.training_insights import Insight
from trellis.training_strength import Exercise, StrengthSession
from trellis.tasks import UNSET, Energy, Priority, Task, TaskStatus, _UnsetType
from trellis.user_context import CurrentContext, TrainingAnchor, UserProfile

register_uuid()


class PostgresDatabase:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg2.connect(self.database_url)

    def migrate(self, migrations_dir: Path) -> None:
        # Bootstrap the migration tracker in its own transaction.
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        filename TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                # If the database already has tables from before schema_migrations
                # was introduced, mark all pre-008 migrations as applied so they
                # are not replayed (some are not idempotent).
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'tasks'
                    )
                    """
                )
                if cur.fetchone()[0]:
                    for mf in sorted(migrations_dir.glob("*.sql")):
                        if mf.name < "008_":
                            cur.execute(
                                "INSERT INTO schema_migrations (filename) VALUES (%s)"
                                " ON CONFLICT DO NOTHING",
                                (mf.name,),
                            )

        # Apply each unapplied migration in its own transaction.
        for migration in sorted(migrations_dir.glob("*.sql")):
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM schema_migrations WHERE filename = %s",
                        (migration.name,),
                    )
                    if cur.fetchone() is not None:
                        continue
                    cur.execute(migration.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)"
                        " ON CONFLICT DO NOTHING",
                        (migration.name,),
                    )

    def ensure_user(self, telegram_user_id: int, timezone: str) -> UUID:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trellis_users (telegram_user_id, timezone)
                    VALUES (%s, %s)
                    ON CONFLICT (telegram_user_id)
                    DO UPDATE SET timezone = EXCLUDED.timezone, updated_at = NOW()
                    RETURNING id
                    """,
                    (telegram_user_id, timezone),
                )
                return cursor.fetchone()[0]

    def list_users(self) -> list[tuple[UUID, int]]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, telegram_user_id
                    FROM trellis_users
                    ORDER BY created_at
                    """
                )
                return [(row[0], row[1]) for row in cursor.fetchall()]


class PostgresTaskRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def create(self, task: Task) -> Task:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tasks (
                        id, user_id, title, status, priority, energy, due_at,
                        source_capture_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task.id,
                        task.user_id,
                        task.title,
                        task.status.value,
                        task.priority.value,
                        task.energy.value,
                        task.due_at,
                        task.source_capture_id,
                        task.created_at,
                    ),
                )
                self._event(cursor, task, "created", {"title": task.title})
        return task

    def list_open(self, user_id: UUID) -> list[Task]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s AND status IN ('open', 'in_progress')
                    ORDER BY due_at NULLS LAST, created_at
                    """,
                    (user_id,),
                )
                return [self._task(row) for row in cursor.fetchall()]

    def find_open_by_title(self, user_id: UUID, title: str) -> list[Task]:
        normalized = " ".join(title.split())
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s
                      AND status IN ('open', 'in_progress')
                      AND lower(title) = lower(%s)
                    ORDER BY created_at
                    """,
                    (user_id, normalized),
                )
                rows = cursor.fetchall()
                if not rows:
                    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    cursor.execute(
                        """
                        SELECT * FROM tasks
                        WHERE user_id = %s
                          AND status IN ('open', 'in_progress')
                          AND lower(title) LIKE lower(%s) ESCAPE '\\'
                        ORDER BY created_at
                        LIMIT 5
                        """,
                        (user_id, f"%{escaped}%"),
                    )
                    rows = cursor.fetchall()
                return [self._task(row) for row in rows]

    def complete(self, task_id: UUID, completed_at: datetime) -> Task:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE tasks
                    SET status = 'done', completed_at = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (completed_at, task_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(task_id)
                task = self._task(row)
                self._event(cursor, task, "completed", {})
                return task

    def archive(self, task_id: UUID) -> Task:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE tasks
                    SET status = 'archived', updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (task_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(task_id)
                task = self._task(row)
                self._event(cursor, task, "archived", {})
                return task

    def update_task(
        self,
        task_id: UUID,
        *,
        new_title: str | None = None,
        due_at: datetime | None | _UnsetType = UNSET,
        priority: str | None = None,
        energy: str | None = None,
    ) -> Task:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM tasks WHERE id = %s AND status IN ('open', 'in_progress')",
                    (task_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(task_id)
                old_task = self._task(row)

                if new_title is not None and new_title != old_task.title:
                    cursor.execute(
                        """
                        SELECT id FROM tasks
                        WHERE user_id = %s AND id <> %s
                          AND status IN ('open', 'in_progress')
                          AND lower(title) = lower(%s)
                        LIMIT 1
                        """,
                        (old_task.user_id, task_id, new_title),
                    )
                    if cursor.fetchone() is not None:
                        raise ValueError(f"A task named '{new_title}' already exists")

                fields: list[str] = ["updated_at = NOW()"]
                params: list = []
                if new_title is not None:
                    fields.append("title = %s")
                    params.append(new_title)
                if not isinstance(due_at, _UnsetType):
                    fields.append("due_at = %s")
                    params.append(due_at)
                if priority is not None:
                    fields.append("priority = %s")
                    params.append(priority)
                if energy is not None:
                    fields.append("energy = %s")
                    params.append(energy)
                params.append(task_id)

                cursor.execute(
                    f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s RETURNING *",
                    params,
                )
                updated = self._task(cursor.fetchone())
                context: dict = {}
                if new_title is not None:
                    context["old_title"] = old_task.title
                    context["new_title"] = new_title
                if not isinstance(due_at, _UnsetType):
                    context["due_at"] = due_at.isoformat() if due_at else None
                if priority is not None:
                    context["priority"] = priority
                if energy is not None:
                    context["energy"] = energy
                self._event(cursor, updated, "updated", context)
                return updated

    def list_completed(self, user_id: UUID, limit: int = 20) -> list[Task]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s AND status = 'done'
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [self._task(row) for row in cursor.fetchall()]

    @staticmethod
    def _task(row: dict) -> Task:
        return Task(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            priority=Priority(row["priority"]),
            energy=Energy(row["energy"]),
            due_at=row["due_at"],
            source_capture_id=row["source_capture_id"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _event(cursor, task: Task, event_type: str, context: dict) -> None:
        cursor.execute(
            """
            INSERT INTO task_events (task_id, user_id, event_type, context)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (task.id, task.user_id, event_type, json.dumps(context)),
        )


class PostgresReminderRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def schedule(self, reminder: ReminderIntent) -> ReminderIntent:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO reminders (
                        id, task_id, user_id, remind_at, status, created_at, label
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        reminder.id,
                        reminder.task_id,
                        reminder.user_id,
                        reminder.remind_at,
                        reminder.status.value,
                        reminder.created_at,
                        reminder.task_title if reminder.task_id is None else None,
                    ),
                )
                return self._reminder(cursor.fetchone(), reminder.task_title)

    def due_between(
        self,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> list[ReminderIntent]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT reminders.*,
                           COALESCE(tasks.title, reminders.label) AS task_title
                    FROM reminders
                    LEFT JOIN tasks ON tasks.id = reminders.task_id
                    WHERE reminders.user_id = %s
                      AND reminders.status = 'scheduled'
                      AND reminders.remind_at >= %s
                      AND reminders.remind_at <= %s
                    ORDER BY reminders.remind_at
                    """,
                    (user_id, start_at, end_at),
                )
                return [
                    self._reminder(row, row["task_title"] or "")
                    for row in cursor.fetchall()
                ]

    def list_scheduled(self, user_id: UUID) -> list[ReminderIntent]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT reminders.*,
                           COALESCE(tasks.title, reminders.label) AS task_title
                    FROM reminders
                    LEFT JOIN tasks ON tasks.id = reminders.task_id
                    WHERE reminders.user_id = %s
                      AND reminders.status = 'scheduled'
                    ORDER BY reminders.remind_at
                    """,
                    (user_id,),
                )
                return [
                    self._reminder(row, row["task_title"] or "")
                    for row in cursor.fetchall()
                ]

    def cancel(self, reminder_id: UUID) -> ReminderIntent:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE reminders SET status = 'cancelled'
                    WHERE id = %s
                    RETURNING *
                    """,
                    (reminder_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(reminder_id)
                # Fetch label from task if linked
                label = row.get("label") or ""
                if row["task_id"] and not label:
                    with connection.cursor(cursor_factory=RealDictCursor) as cur2:
                        cur2.execute("SELECT title FROM tasks WHERE id = %s", (row["task_id"],))
                        task_row = cur2.fetchone()
                        if task_row:
                            label = task_row["title"]
                return self._reminder(row, label)

    def mark_sent(self, reminder_id: UUID) -> ReminderIntent:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE reminders
                    SET status = 'sent'
                    WHERE id = %s AND status = 'scheduled'
                    RETURNING *
                    """,
                    (reminder_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(reminder_id)
                label = row.get("label") or ""
                if row["task_id"] and not label:
                    with connection.cursor(cursor_factory=RealDictCursor) as cur2:
                        cur2.execute("SELECT title FROM tasks WHERE id = %s", (row["task_id"],))
                        task_row = cur2.fetchone()
                        if task_row:
                            label = task_row["title"]
                return self._reminder(row, label)

    @staticmethod
    def _reminder(row: dict, task_title: str) -> ReminderIntent:
        return ReminderIntent(
            id=row["id"],
            user_id=row["user_id"],
            task_id=row["task_id"],
            task_title=task_title,
            remind_at=row["remind_at"],
            status=ReminderStatus(row["status"]),
            created_at=row["created_at"],
        )


class PostgresCaptureRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def create_pending(self, user_id: UUID, content: str) -> Capture:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO captures (user_id, content)
                    VALUES (%s, %s)
                    RETURNING *
                    """,
                    (user_id, content),
                )
                return self._capture(cursor.fetchone())

    def mark_processed(
        self,
        capture_id: UUID,
        interpretation: Interpretation,
    ) -> Capture:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE captures
                    SET synthesis = %s,
                        observations = %s::jsonb,
                        questions = %s::jsonb,
                        decisions = %s::jsonb,
                        processing_status = 'processed',
                        processing_error = NULL,
                        processed_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        interpretation.synthesis,
                        json.dumps(interpretation.observations),
                        json.dumps(interpretation.questions),
                        json.dumps(interpretation.decisions),
                        capture_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise LookupError(capture_id)
                return self._capture(row)

    def mark_failed(self, capture_id: UUID, error: str) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE captures
                    SET processing_status = 'failed',
                        processing_error = %s,
                        processed_at = NOW()
                    WHERE id = %s
                    """,
                    (error[:2000], capture_id),
                )

    def save(self, user_id: UUID, raw: str, synthesis: str) -> Capture:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO captures (user_id, content, synthesis, processing_status, processed_at)
                    VALUES (%s, %s, %s, 'processed', NOW())
                    RETURNING *
                    """,
                    (user_id, raw, synthesis),
                )
                return self._capture(cursor.fetchone())

    def list_recent(self, user_id: UUID, limit: int = 20) -> list[Capture]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM captures
                    WHERE user_id = %s
                      AND processing_status = 'processed'
                      AND synthesis IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [self._capture(row) for row in cursor.fetchall()]

    def search_recent(self, user_id: UUID, reference: str, limit: int = 10) -> list[Capture]:
        clean = " ".join(reference.split()).strip()
        escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM captures
                    WHERE user_id = %s
                      AND processing_status = 'processed'
                      AND synthesis IS NOT NULL
                      AND (
                          lower(content) LIKE lower(%s) ESCAPE '\\'
                          OR lower(synthesis) LIKE lower(%s) ESCAPE '\\'
                      )
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, f"%{escaped}%", f"%{escaped}%", limit),
                )
                return [self._capture(row) for row in cursor.fetchall()]

    @staticmethod
    def _capture(row: dict) -> Capture:
        return Capture(
            id=row["id"],
            user_id=row["user_id"],
            content=row["content"],
            created_at=row["created_at"],
            synthesis=row["synthesis"],
            observations=tuple(row["observations"] or []),
            questions=tuple(row["questions"] or []),
            decisions=tuple(row["decisions"] or []),
        )


class PostgresIdeaRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def create_or_get(
        self,
        user_id: UUID,
        title: str,
        synthesis: str,
        source_capture_id: UUID,
    ) -> tuple[Idea, bool]:
        clean_title = " ".join(title.split()).strip(" .")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM ideas
                    WHERE user_id = %s
                      AND status IN ('inbox', 'incubating', 'active')
                      AND lower(title) = lower(%s)
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (user_id, clean_title),
                )
                existing = cursor.fetchone()
                if existing:
                    return self._idea(existing), False

                cursor.execute(
                    """
                    INSERT INTO ideas (
                        user_id, title, synthesis, source_capture_id
                    ) VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (user_id, clean_title, synthesis, source_capture_id),
                )
                return self._idea(cursor.fetchone()), True

    def save(self, user_id: UUID, title: str, synthesis: str) -> Idea:
        clean_title = " ".join(title.split()).strip(" .")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO ideas (user_id, title, synthesis)
                    VALUES (%s, %s, %s)
                    RETURNING *
                    """,
                    (user_id, clean_title, synthesis),
                )
                return self._idea(cursor.fetchone())

    def list_inbox(self, user_id: UUID) -> list[Idea]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM ideas
                    WHERE user_id = %s
                      AND status IN ('inbox', 'incubating')
                    ORDER BY created_at
                    """,
                    (user_id,),
                )
                return [self._idea(row) for row in cursor.fetchall()]

    def search_inbox(self, user_id: UUID, reference: str) -> list[Idea]:
        clean = " ".join(reference.split()).strip()
        if not clean:
            return self.list_inbox(user_id)
        escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM ideas
                    WHERE user_id = %s
                      AND status IN ('inbox', 'incubating')
                      AND (
                          lower(title) LIKE lower(%s) ESCAPE '\\'
                          OR lower(synthesis) LIKE lower(%s) ESCAPE '\\'
                      )
                    ORDER BY created_at
                    """,
                    (user_id, f"%{escaped}%", f"%{escaped}%"),
                )
                return [self._idea(row) for row in cursor.fetchall()]

    @staticmethod
    def _idea(row: dict) -> Idea:
        return Idea(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            synthesis=row["synthesis"],
            source_capture_id=row["source_capture_id"],
            created_at=row["created_at"],
        )


class PostgresCorrectionRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def apply_task_to_idea(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s
                      AND id = ANY(%s)
                      AND status IN ('open', 'in_progress')
                    FOR UPDATE
                    """,
                    (user_id, list(plan.task_ids)),
                )
                rows = cursor.fetchall()
                if {row["id"] for row in rows} != set(plan.task_ids):
                    raise ValueError("One or more correction tasks are no longer active")
                tasks = tuple(PostgresTaskRepository._task(row) for row in rows)

                if plan.target_idea_id:
                    cursor.execute(
                        """
                        UPDATE ideas
                        SET title = %s, synthesis = %s, updated_at = NOW()
                        WHERE id = %s AND user_id = %s
                        RETURNING *
                        """,
                        (
                            plan.idea_title,
                            plan.idea_synthesis,
                            plan.target_idea_id,
                            user_id,
                        ),
                    )
                else:
                    source_capture_id = next(
                        (
                            task.source_capture_id
                            for task in tasks
                            if task.source_capture_id is not None
                        ),
                        None,
                    )
                    cursor.execute(
                        """
                        INSERT INTO ideas (
                            user_id, title, synthesis, source_capture_id
                        ) VALUES (%s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            user_id,
                            plan.idea_title,
                            plan.idea_synthesis,
                            source_capture_id,
                        ),
                    )
                idea_row = cursor.fetchone()
                if idea_row is None:
                    raise ValueError("Target idea is unavailable")
                idea = PostgresIdeaRepository._idea(idea_row)

                cursor.execute(
                    """
                    UPDATE tasks
                    SET status = 'archived', updated_at = NOW()
                    WHERE user_id = %s AND id = ANY(%s)
                    """,
                    (user_id, list(plan.task_ids)),
                )
                for task in tasks:
                    cursor.execute(
                        """
                        INSERT INTO task_events (
                            task_id, user_id, event_type, reason, context
                        ) VALUES (%s, %s, 'reclassified_as_idea', %s, %s::jsonb)
                        """,
                        (
                            task.id,
                            user_id,
                            instruction,
                            json.dumps({"idea_id": str(idea.id)}),
                        ),
                    )

                cursor.execute(
                    """
                    INSERT INTO correction_events (
                        user_id, instruction, action_type, affected_task_ids,
                        affected_idea_id, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING created_at
                    """,
                    (
                        user_id,
                        instruction,
                        plan.action,
                        list(plan.task_ids),
                        idea.id,
                        plan.summary,
                    ),
                )
                created_at = cursor.fetchone()["created_at"]
                return CorrectionResult(
                    action=plan.action,
                    summary=plan.summary,
                    created_at=created_at,
                    idea=idea,
                    archived_tasks=tasks,
                )

    def apply_idea_to_task(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult:
        idea_id = plan.idea_ids[0]
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM ideas
                    WHERE user_id = %s
                      AND id = %s
                      AND status IN ('inbox', 'incubating')
                    FOR UPDATE
                    """,
                    (user_id, idea_id),
                )
                idea_row = cursor.fetchone()
                if idea_row is None:
                    raise ValueError("Correction idea is no longer in the inbox")
                idea = PostgresIdeaRepository._idea(idea_row)

                cursor.execute(
                    """
                    SELECT id FROM tasks
                    WHERE user_id = %s
                      AND status IN ('open', 'in_progress')
                      AND lower(title) = lower(%s)
                    LIMIT 1
                    """,
                    (user_id, plan.task_title),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("An open task with that title already exists")

                task_id = uuid4()
                cursor.execute(
                    """
                    INSERT INTO tasks (
                        id, user_id, title, source_capture_id
                    ) VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (task_id, user_id, plan.task_title, idea.source_capture_id),
                )
                task = PostgresTaskRepository._task(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO task_events (
                        task_id, user_id, event_type, reason, context
                    ) VALUES (%s, %s, 'created_from_idea', %s, %s::jsonb)
                    """,
                    (
                        task.id,
                        user_id,
                        instruction,
                        json.dumps({"idea_id": str(idea.id)}),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE ideas
                    SET status = 'archived', updated_at = NOW()
                    WHERE id = %s AND user_id = %s
                    """,
                    (idea.id, user_id),
                )
                cursor.execute(
                    """
                    INSERT INTO correction_events (
                        user_id, instruction, action_type, affected_task_ids,
                        affected_idea_id, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING created_at
                    """,
                    (
                        user_id,
                        instruction,
                        plan.action,
                        [task.id],
                        idea.id,
                        plan.summary,
                    ),
                )
                created_at = cursor.fetchone()["created_at"]
                return CorrectionResult(
                    action=plan.action,
                    summary=plan.summary,
                    created_at=created_at,
                    task=task,
                    archived_ideas=(idea,),
                )

    def apply_rename_task(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult:
        task_id = plan.task_ids[0]
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM tasks
                    WHERE user_id = %s
                      AND id = %s
                      AND status IN ('open', 'in_progress')
                    FOR UPDATE
                    """,
                    (user_id, task_id),
                )
                original_row = cursor.fetchone()
                if original_row is None:
                    raise ValueError("Correction task is no longer active")
                original = PostgresTaskRepository._task(original_row)

                cursor.execute(
                    """
                    SELECT id FROM tasks
                    WHERE user_id = %s
                      AND id <> %s
                      AND status IN ('open', 'in_progress')
                      AND lower(title) = lower(%s)
                    LIMIT 1
                    """,
                    (user_id, task_id, plan.task_title),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("An open task with that title already exists")

                cursor.execute(
                    """
                    UPDATE tasks
                    SET title = %s, updated_at = NOW()
                    WHERE id = %s AND user_id = %s
                    RETURNING *
                    """,
                    (plan.task_title, task_id, user_id),
                )
                task = PostgresTaskRepository._task(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO task_events (
                        task_id, user_id, event_type, reason, context
                    ) VALUES (%s, %s, 'renamed', %s, %s::jsonb)
                    """,
                    (
                        task.id,
                        user_id,
                        instruction,
                        json.dumps(
                            {
                                "old_title": original.title,
                                "new_title": task.title,
                            }
                        ),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO correction_events (
                        user_id, instruction, action_type, affected_task_ids,
                        summary
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING created_at
                    """,
                    (
                        user_id,
                        instruction,
                        plan.action,
                        [task.id],
                        plan.summary,
                    ),
                )
                created_at = cursor.fetchone()["created_at"]
                return CorrectionResult(
                    action=plan.action,
                    summary=plan.summary,
                    created_at=created_at,
                    task=task,
                    archived_tasks=(original,),
                )


class PostgresCycleRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def record(self, event: CycleEvent) -> CycleEvent:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO cycle_events (id, user_id, event_type, occurred_on, note, symptoms)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING *
                    """,
                    (
                        event.id,
                        event.user_id,
                        event.event_type,
                        event.occurred_on,
                        event.note,
                        json.dumps(list(event.symptoms)),
                    ),
                )
                return self._event(cursor.fetchone())

    def list_recent(self, user_id: UUID, *, limit: int = 10) -> list[CycleEvent]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM cycle_events
                    WHERE user_id = %s
                    ORDER BY occurred_on DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [self._event(row) for row in cursor.fetchall()]

    def last_period_start(self, user_id: UUID) -> CycleEvent | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM cycle_events
                    WHERE user_id = %s AND event_type = 'period_start'
                    ORDER BY occurred_on DESC, created_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
                return self._event(row) if row else None

    @staticmethod
    def _event(row: dict) -> CycleEvent:
        return CycleEvent(
            id=row["id"],
            user_id=row["user_id"],
            event_type=row["event_type"],
            occurred_on=row["occurred_on"],
            note=row["note"],
            symptoms=tuple(row["symptoms"] or []),
            created_at=row["created_at"],
        )


class PostgresGoalRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create(self, goal: Goal) -> Goal:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO goals
                        (id, user_id, title, goal_type, status, target_date,
                         is_fixed_date, metrics, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        goal.id, goal.user_id, goal.title, goal.goal_type,
                        goal.status, goal.target_date, goal.is_fixed_date,
                        json.dumps(goal.metrics), goal.notes,
                    ),
                )
                return self._goal(cursor.fetchone())

    def list_active(self, user_id: UUID) -> list[Goal]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM goals
                    WHERE user_id = %s AND status = 'active'
                    ORDER BY created_at
                    """,
                    (user_id,),
                )
                return [self._goal(row) for row in cursor.fetchall()]

    def get(self, goal_id: UUID) -> Goal | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM goals WHERE id = %s",
                    (goal_id,),
                )
                row = cursor.fetchone()
                return self._goal(row) if row else None

    def update(self, goal_id: UUID, **kwargs) -> Goal:
        allowed = {"title", "goal_type", "status", "target_date",
                   "is_fixed_date", "metrics", "notes"}
        fields = ["updated_at = NOW()"]
        params = []
        for key, value in kwargs.items():
            if key not in allowed:
                raise ValueError(f"Cannot update field: {key}")
            if key == "metrics":
                value = json.dumps(value)
            fields.append(f"{key} = %s")
            params.append(value)
        params.append(goal_id)
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    f"UPDATE goals SET {', '.join(fields)} WHERE id = %s RETURNING *",
                    params,
                )
                return self._goal(cursor.fetchone())

    @staticmethod
    def _goal(row: dict) -> Goal:
        return Goal(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            goal_type=row["goal_type"],
            status=row["status"],
            target_date=row["target_date"],
            is_fixed_date=row["is_fixed_date"],
            metrics=row["metrics"] or {},
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class PostgresSessionCompletionRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, completion: SessionCompletion) -> SessionCompletion:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO session_completions (
                        id, user_id, plan_id, session_id, garmin_activity_id,
                        session_kind, planned_on, completed_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, plan_id, session_id) DO UPDATE SET
                        completed_at = EXCLUDED.completed_at,
                        garmin_activity_id = EXCLUDED.garmin_activity_id
                    RETURNING *
                    """,
                    (
                        completion.id,
                        completion.user_id,
                        completion.plan_id,
                        completion.session_id,
                        completion.garmin_activity_id,
                        completion.session_kind,
                        completion.planned_on,
                        completion.completed_at,
                        completion.created_at,
                    ),
                )
                return self._completion(cursor.fetchone())

    def list_for_week(
        self, user_id: UUID, week_start: date
    ) -> list[SessionCompletion]:
        week_end = week_start + timedelta(days=6)
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM session_completions
                    WHERE user_id = %s
                      AND planned_on BETWEEN %s AND %s
                    ORDER BY planned_on
                    """,
                    (user_id, week_start, week_end),
                )
                return [self._completion(row) for row in cursor.fetchall()]

    @staticmethod
    def _completion(row: dict) -> SessionCompletion:
        return SessionCompletion(
            id=row["id"],
            user_id=row["user_id"],
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            garmin_activity_id=row["garmin_activity_id"],
            session_kind=row["session_kind"],
            planned_on=row["planned_on"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
        )


class PostgresWorkoutCheckinRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, checkin: WorkoutCheckin) -> WorkoutCheckin:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO workout_checkins (
                        id, user_id, session_kind, checked_in_on,
                        perceived_effort, feel_note, soreness_note, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        checkin.id,
                        checkin.user_id,
                        checkin.session_kind,
                        checkin.checked_in_on,
                        checkin.perceived_effort,
                        checkin.feel_note,
                        checkin.soreness_note,
                        checkin.created_at,
                    ),
                )
                return self._row(cursor.fetchone())

    def list_recent(self, user_id: UUID, *, limit: int) -> list[WorkoutCheckin]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM workout_checkins
                    WHERE user_id = %s
                    ORDER BY checked_in_on DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [self._row(row) for row in cursor.fetchall()]

    @staticmethod
    def _row(row: dict) -> WorkoutCheckin:
        return WorkoutCheckin(
            id=row["id"],
            user_id=row["user_id"],
            session_kind=row["session_kind"],
            checked_in_on=row["checked_in_on"],
            perceived_effort=row["perceived_effort"],
            feel_note=row["feel_note"],
            soreness_note=row["soreness_note"],
            created_at=row["created_at"],
        )


class PostgresStrengthSessionRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, session: StrengthSession) -> StrengthSession:
        exercises_json = json.dumps([_exercise_to_dict(e) for e in session.exercises])
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO strength_sessions
                        (id, user_id, session_date, program_phase, exercises, notes, created_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING *
                    """,
                    (
                        session.id,
                        session.user_id,
                        session.session_date,
                        session.program_phase,
                        exercises_json,
                        session.notes,
                        session.created_at,
                    ),
                )
                return self._row(cursor.fetchone())

    def list_recent(self, user_id: UUID, *, limit: int) -> list[StrengthSession]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM strength_sessions
                    WHERE user_id = %s
                    ORDER BY session_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [self._row(row) for row in cursor.fetchall()]

    @staticmethod
    def _row(row: dict) -> StrengthSession:
        raw_exercises = row["exercises"] or []
        exercises = tuple(
            Exercise(
                name=e.get("name", ""),
                sets=e.get("sets"),
                reps=e.get("reps"),
                weight_kg=e.get("weight_kg"),
                duration_seconds=e.get("duration_seconds"),
                notes=e.get("notes"),
            )
            for e in raw_exercises
        )
        return StrengthSession(
            id=row["id"],
            user_id=row["user_id"],
            session_date=row["session_date"],
            exercises=exercises,
            program_phase=row["program_phase"],
            notes=row["notes"],
            created_at=row["created_at"],
        )


def _exercise_to_dict(e: Exercise) -> dict:
    return {k: v for k, v in asdict(e).items() if v is not None}


class PostgresInsightRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, insight: Insight) -> Insight:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO insights
                        (id, user_id, domain, insight_type, summary,
                         evidence_count, confidence, is_active,
                         detected_on, last_confirmed_on, expires_on, metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING *
                    """,
                    (
                        insight.id, insight.user_id, insight.domain,
                        insight.insight_type, insight.summary,
                        insight.evidence_count, insight.confidence,
                        insight.is_active, insight.detected_on,
                        insight.last_confirmed_on, insight.expires_on,
                        json.dumps(insight.metadata),
                    ),
                )
                return self._row(cur.fetchone())

    def upsert_by_type(self, insight: Insight) -> Insight:
        """Insert or update active insight of same type+domain atomically."""
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO insights
                        (id, user_id, domain, insight_type, summary,
                         evidence_count, confidence, is_active,
                         detected_on, last_confirmed_on, expires_on, metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (user_id, domain, insight_type)
                        WHERE is_active = TRUE
                    DO UPDATE SET
                        summary = EXCLUDED.summary,
                        evidence_count = EXCLUDED.evidence_count,
                        confidence = EXCLUDED.confidence,
                        last_confirmed_on = EXCLUDED.last_confirmed_on,
                        expires_on = EXCLUDED.expires_on
                    RETURNING *
                    """,
                    (
                        insight.id, insight.user_id, insight.domain,
                        insight.insight_type, insight.summary,
                        insight.evidence_count, insight.confidence,
                        insight.detected_on, insight.last_confirmed_on,
                        insight.expires_on, json.dumps(insight.metadata),
                    ),
                )
                return self._row(cur.fetchone())

    def list_active(self, user_id: UUID) -> list[Insight]:
        from datetime import date as _date
        today = _date.today()
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM insights
                    WHERE user_id = %s AND is_active = TRUE
                      AND (snooze_until IS NULL OR snooze_until <= %s)
                    ORDER BY confidence DESC, last_confirmed_on DESC
                    """,
                    (user_id, today),
                )
                return [self._row(row) for row in cur.fetchall()]

    def deactivate_stale(self, user_id: UUID, stale_before: date) -> int:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE insights SET is_active = FALSE
                    WHERE user_id = %s AND last_confirmed_on < %s AND is_active = TRUE
                    """,
                    (user_id, stale_before),
                )
                return cur.rowcount

    def respond(self, insight_id: UUID, action: str, note: str | None, today: date) -> None:
        if action == "snooze":
            from datetime import timedelta
            snooze_until = today + timedelta(days=7)
            with self.database.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE insights SET snooze_until = %s WHERE id = %s",
                        (snooze_until, insight_id),
                    )
        elif action in ("resolve", "reject"):
            with self.database.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE insights
                        SET is_active = FALSE, dismissed_reason = %s, dismissed_at = NOW()
                        WHERE id = %s
                        """,
                        (f"{action}: {note}" if note else action, insight_id),
                    )

    @staticmethod
    def _row(row: dict) -> Insight:
        return Insight(
            id=row["id"],
            user_id=row["user_id"],
            domain=row["domain"],
            insight_type=row["insight_type"],
            summary=row["summary"],
            evidence_count=row["evidence_count"],
            confidence=float(row["confidence"]),
            is_active=row["is_active"],
            detected_on=row["detected_on"],
            last_confirmed_on=row["last_confirmed_on"],
            expires_on=row["expires_on"],
            metadata=row["metadata"] or {},
            dismissed_reason=row.get("dismissed_reason"),
            dismissed_at=row.get("dismissed_at"),
            snooze_until=row.get("snooze_until"),
        )


class PostgresArcRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, arc: TrainingArc) -> TrainingArc:
        phases_json = json.dumps([p.to_dict() for p in arc.phases])
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_arcs (id, user_id, goal_id, phases, notes, is_active, generated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s, TRUE, %s)
                    """,
                    (arc.id, arc.user_id, arc.goal_id, phases_json, arc.notes, arc.generated_at),
                )
        return arc

    def get_active(self, user_id: UUID) -> TrainingArc | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, user_id, goal_id, phases, notes, generated_at
                    FROM training_arcs
                    WHERE user_id = %s AND is_active = TRUE
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        phases_raw = row["phases"] if isinstance(row["phases"], list) else json.loads(row["phases"])
        return TrainingArc(
            id=row["id"],
            user_id=row["user_id"],
            goal_id=row["goal_id"],
            phases=[ArcPhase.from_dict(p) for p in phases_raw],
            notes=row["notes"],
            generated_at=row["generated_at"],
        )

    def deactivate_all(self, user_id: UUID) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_arcs SET is_active = FALSE WHERE user_id = %s",
                    (user_id,),
                )

    def deactivate_others(self, user_id: UUID, keep_id: UUID) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_arcs SET is_active = FALSE WHERE user_id = %s AND id != %s",
                    (user_id, keep_id),
                )


class PostgresTrainingAnchorRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list_active(self, user_id: UUID) -> list[TrainingAnchor]:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM training_anchors
                    WHERE user_id = %s AND is_active = TRUE
                    ORDER BY day_of_week, time_of_day NULLS LAST
                    """,
                    (user_id,),
                )
                return [self._row(row) for row in cur.fetchall()]

    def save(self, anchor: TrainingAnchor) -> TrainingAnchor:
        time_str = anchor.time_of_day  # stored as TEXT "HH:MM", cast to TIME in SQL
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_anchors
                        (id, user_id, day_of_week, time_of_day, kind, label, is_hard_constraint, is_active)
                    VALUES (%s, %s, %s, %s::time, %s, %s, %s, TRUE)
                    """,
                    (
                        anchor.id, anchor.user_id, anchor.day_of_week,
                        time_str, anchor.kind, anchor.label, anchor.is_hard_constraint,
                    ),
                )
        return anchor

    def deactivate(self, anchor_id: UUID) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_anchors SET is_active = FALSE WHERE id = %s",
                    (anchor_id,),
                )

    @staticmethod
    def _row(row: dict) -> TrainingAnchor:
        time_val = row["time_of_day"]
        time_str = time_val.strftime("%H:%M") if time_val is not None else None
        return TrainingAnchor(
            id=row["id"],
            user_id=row["user_id"],
            day_of_week=row["day_of_week"],
            time_of_day=time_str,
            kind=row["kind"],
            label=row["label"],
            is_hard_constraint=row["is_hard_constraint"],
        )


class PostgresUserProfileRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID) -> UserProfile | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM user_profile WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return self._row(row) if row else None

    def upsert(self, profile: UserProfile) -> UserProfile:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO user_profile (user_id, name, physical_notes, cognitive_notes, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        physical_notes = EXCLUDED.physical_notes,
                        cognitive_notes = EXCLUDED.cognitive_notes,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (profile.user_id, profile.name, profile.physical_notes, profile.cognitive_notes, profile.updated_at),
                )
                return self._row(cur.fetchone())

    @staticmethod
    def _row(row: dict) -> UserProfile:
        return UserProfile(
            user_id=row["user_id"],
            name=row["name"],
            physical_notes=row["physical_notes"],
            cognitive_notes=row["cognitive_notes"],
            updated_at=row["updated_at"],
        )


class PostgresCurrentContextRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID) -> CurrentContext | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM current_context WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return self._row(row) if row else None

    def upsert(self, context: CurrentContext) -> CurrentContext:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO current_context
                        (user_id, physical_notes, cognitive_notes, misc_notes, valid_until, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        physical_notes = EXCLUDED.physical_notes,
                        cognitive_notes = EXCLUDED.cognitive_notes,
                        misc_notes = EXCLUDED.misc_notes,
                        valid_until = EXCLUDED.valid_until,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        context.user_id, context.physical_notes, context.cognitive_notes,
                        context.misc_notes, context.valid_until, context.updated_at,
                    ),
                )
                return self._row(cur.fetchone())

    @staticmethod
    def _row(row: dict) -> CurrentContext:
        return CurrentContext(
            user_id=row["user_id"],
            physical_notes=row["physical_notes"],
            cognitive_notes=row["cognitive_notes"],
            misc_notes=row["misc_notes"],
            valid_until=row["valid_until"],
            updated_at=row["updated_at"],
        )


class PostgresLearningThreadRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, thread: LearningThread) -> LearningThread:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO learning_threads (id, user_id, name, description, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        is_active = EXCLUDED.is_active
                    RETURNING *
                    """,
                    (thread.id, thread.user_id, thread.name, thread.description,
                     thread.is_active, thread.created_at),
                )
                return self._row(cur.fetchone())

    def get(self, thread_id: UUID) -> LearningThread | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM learning_threads WHERE id = %s", (thread_id,))
                row = cur.fetchone()
        return self._row(row) if row else None

    def list_active(self, user_id: UUID) -> list[LearningThread]:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM learning_threads WHERE user_id = %s AND is_active ORDER BY created_at",
                    (user_id,),
                )
                return [self._row(r) for r in cur.fetchall()]

    def deactivate(self, thread_id: UUID) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE learning_threads SET is_active = FALSE WHERE id = %s",
                    (thread_id,),
                )

    @staticmethod
    def _row(row: dict) -> LearningThread:
        return LearningThread(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            description=row["description"],
            is_active=row["is_active"],
            created_at=row["created_at"],
        )


class PostgresLearningEntryRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def save(self, entry: LearningEntry) -> LearningEntry:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO learning_entries (id, user_id, thread_id, summary, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (entry.id, entry.user_id, entry.thread_id, entry.summary, entry.created_at),
                )
                return self._row(cur.fetchone())

    def recent(self, thread_id: UUID, *, limit: int) -> list[LearningEntry]:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM learning_entries
                    WHERE thread_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (thread_id, limit),
                )
                rows = cur.fetchall()
        return list(reversed([self._row(r) for r in rows]))

    @staticmethod
    def _row(row: dict) -> LearningEntry:
        return LearningEntry(
            id=row["id"],
            user_id=row["user_id"],
            thread_id=row["thread_id"],
            summary=row["summary"],
            created_at=row["created_at"],
        )


class PostgresPreferencesRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID, domain: str) -> str | None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM user_preferences WHERE user_id = %s AND domain = %s",
                    (user_id, domain),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def set(self, user_id: UUID, domain: str, content: str) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, domain, content, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id, domain) DO UPDATE
                        SET content = EXCLUDED.content, updated_at = NOW()
                    """,
                    (user_id, domain, content),
                )
