from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, time, timedelta
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.postgres import PostgresDatabase
from trellis.training import (
    Intensity,
    PlanMode,
    SessionBlock,
    SessionKind,
    TrainingGoal,
    TrainingSession,
    Weekday,
    WeeklyPlan,
)


class PostgresTrainingRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def save_active(self, user_id: UUID, plan: WeeklyPlan) -> WeeklyPlan:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(revision), 0)
                    FROM training_plans
                    WHERE user_id = %s
                      AND week_start = %s
                    """,
                    (user_id, plan.week_start),
                )
                latest_revision = cursor.fetchone()[0]
                if plan.revision <= latest_revision:
                    plan = replace(plan, revision=latest_revision + 1)

                cursor.execute(
                    """
                    UPDATE training_plans
                    SET status = 'superseded', updated_at = NOW()
                    WHERE user_id = %s
                      AND week_start = %s
                      AND status = 'active'
                    """,
                    (user_id, plan.week_start),
                )
                cursor.execute(
                    """
                    INSERT INTO training_plans (
                        id, user_id, week_start, mode, revision, status, rationale
                    ) VALUES (%s, %s, %s, %s, %s, 'active', %s::jsonb)
                    """,
                    (
                        plan.id,
                        user_id,
                        plan.week_start,
                        plan.mode.value,
                        plan.revision,
                        json.dumps(list(plan.rationale)),
                    ),
                )
                for session in plan.sessions:
                    scheduled_for = self._scheduled_for(plan, session)
                    cursor.execute(
                        """
                        INSERT INTO training_sessions (
                            id, plan_id, user_id, scheduled_for, scheduled_day, kind,
                            title, intensity, planned_duration_minutes, fixed_anchor,
                            notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            session.id,
                            plan.id,
                            user_id,
                            scheduled_for,
                            int(session.day),
                            session.kind.value,
                            session.title,
                            session.intensity.value,
                            session.total_minutes,
                            session.fixed_anchor,
                            json.dumps(list(session.notes)),
                        ),
                    )
                    for position, block in enumerate(session.blocks):
                        cursor.execute(
                            """
                            INSERT INTO training_session_blocks (
                                session_id, position, name, duration_minutes,
                                instructions
                            ) VALUES (%s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                session.id,
                                position,
                                block.name,
                                block.duration_minutes,
                                json.dumps(list(block.instructions)),
                            ),
                        )
        return plan

    def latest_active(self, user_id: UUID, week_start) -> WeeklyPlan | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM training_plans
                    WHERE user_id = %s
                      AND week_start = %s
                      AND status = 'active'
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (user_id, week_start),
                )
                plan_row = cursor.fetchone()
                if plan_row is None:
                    return None

                cursor.execute(
                    """
                    SELECT *
                    FROM training_sessions
                    WHERE plan_id = %s
                    ORDER BY scheduled_day, created_at
                    """,
                    (plan_row["id"],),
                )
                session_rows = cursor.fetchall()
                sessions = []
                for row in session_rows:
                    cursor.execute(
                        """
                        SELECT *
                        FROM training_session_blocks
                        WHERE session_id = %s
                        ORDER BY position
                        """,
                        (row["id"],),
                    )
                    blocks = tuple(
                        SessionBlock(
                            name=block["name"],
                            duration_minutes=block["duration_minutes"],
                            instructions=tuple(block["instructions"]),
                        )
                        for block in cursor.fetchall()
                    )
                    scheduled_for = row["scheduled_for"]
                    sessions.append(
                        TrainingSession(
                            id=row["id"],
                            day=Weekday(row["scheduled_day"]),
                            kind=SessionKind(row["kind"]),
                            title=row["title"],
                            intensity=Intensity(row["intensity"]),
                            blocks=blocks,
                            start_time=(
                                time(
                                    scheduled_for.hour,
                                    scheduled_for.minute,
                                )
                                if scheduled_for is not None
                                else None
                            ),
                            fixed_anchor=row["fixed_anchor"],
                            notes=tuple(row["notes"] or ()),
                        )
                    )

                return WeeklyPlan(
                    id=plan_row["id"],
                    week_start=plan_row["week_start"],
                    goal=TrainingGoal(),
                    mode=PlanMode(plan_row["mode"]),
                    sessions=tuple(sessions),
                    rationale=tuple(plan_row["rationale"] or ()),
                    revision=plan_row["revision"],
                )

    @staticmethod
    def _scheduled_for(plan: WeeklyPlan, session: TrainingSession) -> datetime | None:
        if session.start_time is None:
            return None
        session_date = plan.week_start + timedelta(days=int(session.day))
        return datetime.combine(session_date, session.start_time)
