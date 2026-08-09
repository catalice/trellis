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

from trellis.domain_move_models import RunLog, TrainingPlan

_log = logging.getLogger(__name__)


class TrainingRepository(Protocol):
    def get(self, user_id: UUID) -> TrainingPlan | None: ...
    def upsert(self, record: TrainingPlan) -> TrainingPlan: ...
    def add_run(self, run: RunLog) -> RunLog: ...
    def update_run_note(self, user_id: UUID, run_id, note: str) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_runs SET note = %s WHERE id = %s AND user_id = %s",
                    (note, run_id, user_id),
                )
                return cur.rowcount > 0

    def recent_runs(self, user_id: UUID, *, limit: int) -> list[RunLog]: ...
    def update_run_note(self, user_id: UUID, run_id: UUID, note: str) -> bool: ...


class PostgresMoveRepository:
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

    def add_run(self, run: RunLog) -> RunLog:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_runs
                        (id, user_id, ran_on, note, distance_km, garmin_activity_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run.id, run.user_id, run.ran_on, run.note,
                        run.distance_km, run.garmin_activity_id, run.created_at,
                    ),
                )
        return run

    def update_run_note(self, user_id: UUID, run_id, note: str) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_runs SET note = %s WHERE id = %s AND user_id = %s",
                    (note, run_id, user_id),
                )
                return cur.rowcount > 0

    def recent_runs(self, user_id: UUID, *, limit: int) -> list[RunLog]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM training_runs
                    WHERE user_id = %s
                    ORDER BY ran_on DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [_run_row(r) for r in cur.fetchall()]


def _row(row: dict) -> TrainingPlan:
    return TrainingPlan(
        user_id=row["user_id"],
        goal_id=row.get("goal_id"),
        baseline=row.get("baseline"),
        plan=row.get("plan") or {},
        updated_at=row["updated_at"],
    )


def _run_row(row: dict) -> RunLog:
    return RunLog(
        id=row["id"],
        user_id=row["user_id"],
        ran_on=row["ran_on"],
        note=row["note"],
        distance_km=float(row["distance_km"]) if row.get("distance_km") is not None else None,
        garmin_activity_id=row.get("garmin_activity_id"),
        created_at=row["created_at"],
    )
