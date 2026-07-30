"""
Training storage — Protocol at top, Postgres impl below. Stores the generated
plan and its dated sessions; the arc content itself is Claude's, this only
persists it. Returns typed dataclasses, never strings.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.domain_training_models import (
    PlannedSession,
    PlanStatus,
    SessionStatus,
    SessionType,
    TrainingPlan,
)

_log = logging.getLogger(__name__)


class TrainingRepository(Protocol):
    def supersede_active(self, user_id: UUID) -> None: ...
    def save_plan(self, plan: TrainingPlan) -> TrainingPlan: ...
    def get_active_plan(self, user_id: UUID) -> TrainingPlan | None: ...
    def save_sessions(self, sessions: list[PlannedSession]) -> None: ...
    def list_sessions(self, user_id: UUID, *, start: date, end: date) -> list[PlannedSession]: ...
    def session_on(self, user_id: UUID, on_date: date) -> PlannedSession | None: ...
    def get_session(self, session_id: UUID) -> PlannedSession | None: ...
    def update_session_status(self, session_id: UUID, status: SessionStatus) -> PlannedSession: ...


class PostgresTrainingRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def supersede_active(self, user_id: UUID) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_plans SET status = 'superseded', updated_at = NOW() "
                    "WHERE user_id = %s AND status = 'active'",
                    (user_id,),
                )

    def save_plan(self, plan: TrainingPlan) -> TrainingPlan:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_plans
                        (id, user_id, goal_id, goal_snapshot, rationale, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        plan.id, plan.user_id, plan.goal_id, plan.goal_snapshot,
                        plan.rationale, str(plan.status), plan.created_at, plan.updated_at,
                    ),
                )
        return plan

    def get_active_plan(self, user_id: UUID) -> TrainingPlan | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM training_plans WHERE user_id = %s AND status = 'active' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (user_id,),
                )
                row = cur.fetchone()
                return _plan(row) if row else None

    def save_sessions(self, sessions: list[PlannedSession]) -> None:
        if not sessions:
            return
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                for s in sessions:
                    cur.execute(
                        """
                        INSERT INTO training_sessions
                            (id, plan_id, user_id, scheduled_date, session_type, description,
                             planned_distance_km, planned_duration_min, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            s.id, s.plan_id, s.user_id, s.scheduled_date, str(s.session_type),
                            s.description, s.planned_distance_km, s.planned_duration_min,
                            str(s.status), s.created_at,
                        ),
                    )

    def list_sessions(self, user_id: UUID, *, start: date, end: date) -> list[PlannedSession]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM training_sessions
                    WHERE user_id = %s AND scheduled_date >= %s AND scheduled_date <= %s
                    ORDER BY scheduled_date
                    """,
                    (user_id, start, end),
                )
                return [_session(r) for r in cur.fetchall()]

    def session_on(self, user_id: UUID, on_date: date) -> PlannedSession | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT s.* FROM training_sessions s
                    JOIN training_plans p ON p.id = s.plan_id
                    WHERE s.user_id = %s AND s.scheduled_date = %s AND p.status = 'active'
                    ORDER BY s.created_at DESC LIMIT 1
                    """,
                    (user_id, on_date),
                )
                row = cur.fetchone()
                return _session(row) if row else None

    def get_session(self, session_id: UUID) -> PlannedSession | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM training_sessions WHERE id = %s", (session_id,))
                row = cur.fetchone()
                return _session(row) if row else None

    def update_session_status(self, session_id: UUID, status: SessionStatus) -> PlannedSession:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "UPDATE training_sessions SET status = %s WHERE id = %s RETURNING *",
                    (str(status), session_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(session_id)
                return _session(row)


# ---------------------------------------------------------------------------
# Row -> dataclass
# ---------------------------------------------------------------------------

def _plan(row: dict) -> TrainingPlan:
    return TrainingPlan(
        id=row["id"],
        user_id=row["user_id"],
        goal_id=row.get("goal_id"),
        goal_snapshot=row.get("goal_snapshot") or "",
        rationale=row.get("rationale") or "",
        status=PlanStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _session(row: dict) -> PlannedSession:
    distance = row.get("planned_distance_km")
    return PlannedSession(
        id=row["id"],
        plan_id=row["plan_id"],
        user_id=row["user_id"],
        scheduled_date=row["scheduled_date"],
        session_type=SessionType(row["session_type"]),
        description=row.get("description") or "",
        planned_distance_km=float(distance) if distance is not None else None,
        planned_duration_min=row.get("planned_duration_min"),
        status=SessionStatus(row["status"]),
        created_at=row["created_at"],
    )
