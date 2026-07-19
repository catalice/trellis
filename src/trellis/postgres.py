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
from trellis.training_arc import ArcPhase, TrainingArc
from trellis.session_completion import SessionCompletion, WorkoutCheckin
from trellis.training_insights import Insight
from trellis.training_strength import Exercise, StrengthSession
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
