"""
Training storage — one row per user. Protocol at top, Postgres below. The plan is
a JSON doc Claude authors in conversation; Python only persists it. Typed data,
never strings.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from trellis.domain_training_models import TrainingPlan

_log = logging.getLogger(__name__)


class TrainingRepository(Protocol):
    def get(self, user_id: UUID) -> TrainingPlan | None: ...
    def upsert(self, record: TrainingPlan) -> TrainingPlan: ...


class PostgresTrainingRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def get(self, user_id: UUID) -> TrainingPlan | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM training_plan WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return _row(row) if row else None

    def upsert(self, record: TrainingPlan) -> TrainingPlan:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_plan (user_id, goal_id, baseline, plan, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        goal_id = EXCLUDED.goal_id,
                        baseline = EXCLUDED.baseline,
                        plan = EXCLUDED.plan,
                        updated_at = NOW()
                    """,
                    (record.user_id, record.goal_id, record.baseline, Json(record.plan)),
                )
        return record


def _row(row: dict) -> TrainingPlan:
    return TrainingPlan(
        user_id=row["user_id"],
        goal_id=row.get("goal_id"),
        baseline=row.get("baseline"),
        plan=row.get("plan") or {},
        updated_at=row["updated_at"],
    )
