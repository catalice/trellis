from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from uuid import uuid4

import psycopg2
from psycopg2.extras import RealDictCursor, register_uuid

from trellis.cycle import CycleEvent
from trellis.learn_models import LearningEntry, LearningThread
from trellis.user_context import CurrentContext, UserProfile

register_uuid()


class PostgresDatabase:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg2.connect(self.database_url)

    def migrate(self, migrations_dir: Path) -> None:
        # Bootstrap the migration tracker in its own transaction.
        # All migrations are idempotent (IF NOT EXISTS), so replaying is safe.
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
