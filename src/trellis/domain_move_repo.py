"""
Training storage — the plan (one row per user) and the logbook view over
garmin_activities. The plan is a JSON doc Claude authors in conversation;
Python only persists it. Typed data, never strings.

The logbook (since migration 017): the watch's record IS the record —
garmin_activities rows — with the user's words in user_note beside it.
Sync upserts never name user_note, so their words are structurally safe.
This repo composes RunLog views from those rows; there is no separate
runs table to drift out of step.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol
from datetime import date, datetime
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from trellis.domain_move_models import PlanProposal, RunLog, TrainingPlan

_log = logging.getLogger(__name__)


class TrainingRepository(Protocol):
    def get(self, user_id: UUID) -> TrainingPlan | None: ...
    def upsert(self, record: TrainingPlan) -> TrainingPlan: ...
    def recent_runs(self, user_id: UUID, *, limit: int) -> list[RunLog]: ...
    def recent_workouts(self, user_id: UUID, *, limit: int) -> list[RunLog]: ...
    def set_user_note(self, user_id: UUID, activity_id: str, note: str) -> bool: ...
    def get_watch_push(self, user_id: UUID, on_date: date, name: str) -> str | None: ...
    def record_watch_push(self, user_id: UUID, on_date: date, name: str, workout_id: str) -> None: ...
    def save_proposal(self, proposal: PlanProposal) -> PlanProposal: ...
    def open_proposal(self, user_id: UUID) -> PlanProposal | None: ...
    def get_proposal(self, user_id: UUID, proposal_id: UUID) -> PlanProposal | None: ...
    def resolve_proposal(self, proposal_id: UUID, status: str, at: datetime) -> bool: ...
    def mark_proposal_delivered(self, proposal_id: UUID, at: datetime) -> None: ...


_ACTIVITY_COLS = (
    "SELECT id, user_id, activity_id, name, activity_type, "
    "start_time_epoch_seconds, distance_meters, average_heart_rate, "
    "maximum_heart_rate, "
    "duration_milliseconds, user_note, updated_at FROM garmin_activities"
)


class PostgresMoveRepository:
    def __init__(self, database: Any, tz: Any = None) -> None:
        from zoneinfo import ZoneInfo
        self._db = database
        self._tz = tz or ZoneInfo("UTC")

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

    def recent_runs(self, user_id: UUID, *, limit: int) -> list[RunLog]:
        """Runs only — feeds baseline math, plan ticks, and run reviews."""
        return self._recent(user_id, limit=limit, runs_only=True)

    def recent_workouts(self, user_id: UUID, *, limit: int) -> list[RunLog]:
        """Every sport the watch recorded — the whole-body logbook."""
        return self._recent(user_id, limit=limit, runs_only=False)

    def _recent(self, user_id: UUID, *, limit: int, runs_only: bool) -> list[RunLog]:
        type_filter = " AND activity_type ILIKE '%%run%%'" if runs_only else ""
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"{_ACTIVITY_COLS} WHERE user_id = %s AND start_time_epoch_seconds IS NOT NULL{type_filter}"
                    " ORDER BY start_time_epoch_seconds DESC NULLS LAST LIMIT %s",
                    (user_id, limit),
                )
                return [_run_row(r, self._tz) for r in cur.fetchall()]

    def set_user_note(self, user_id: UUID, activity_id: str, note: str) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE garmin_activities SET user_note = %s, updated_at = NOW()"
                    " WHERE user_id = %s AND activity_id = %s",
                    (note, user_id, activity_id),
                )
                return cur.rowcount > 0

    def save_proposal(self, proposal: PlanProposal) -> PlanProposal:
        """The new proposal and the retirement of the one it replaces, together:
        there is never a moment with two open."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE plan_proposals SET status = 'superseded', resolved_at = %s"
                    " WHERE user_id = %s AND status = 'open'",
                    (proposal.created_at, proposal.user_id),
                )
                cur.execute(
                    "INSERT INTO plan_proposals (id, user_id, plan, replace_week, status, created_at)"
                    " VALUES (%s, %s, %s, %s, 'open', %s)",
                    (proposal.id, proposal.user_id, Json(proposal.plan), proposal.replace_week, proposal.created_at),
                )
        return proposal

    def open_proposal(self, user_id: UUID) -> PlanProposal | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM plan_proposals WHERE user_id = %s AND status = 'open'", (user_id,))
                row = cur.fetchone()
                return _proposal(row) if row else None

    def get_proposal(self, user_id: UUID, proposal_id: UUID) -> PlanProposal | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM plan_proposals WHERE user_id = %s AND id = %s", (user_id, proposal_id))
                row = cur.fetchone()
                return _proposal(row) if row else None

    def resolve_proposal(self, proposal_id: UUID, status: str, at: datetime) -> bool:
        """Only an OPEN proposal can be resolved, and only once: two presses of
        the same button, or a press racing a replacement, leave one winner."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE plan_proposals SET status = %s, resolved_at = %s WHERE id = %s AND status = 'open'",
                            (status, at, proposal_id))
                return cur.rowcount == 1

    def mark_proposal_delivered(self, proposal_id: UUID, at: datetime) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE plan_proposals SET delivered_at = %s WHERE id = %s", (at, proposal_id))

    def get_watch_push(self, user_id: UUID, on_date: date, name: str) -> str | None:
        """The Garmin workout id Trellis last pushed for this date and name."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT workout_id FROM watch_pushes WHERE user_id = %s AND on_date = %s AND name = %s",
                    (user_id, on_date, name),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def record_watch_push(self, user_id: UUID, on_date: date, name: str, workout_id: str) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_pushes (user_id, on_date, name, workout_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, on_date, name)
                    DO UPDATE SET workout_id = EXCLUDED.workout_id, pushed_at = NOW()
                    """,
                    (user_id, on_date, name, workout_id),
                )


def _proposal(row: dict) -> PlanProposal:
    return PlanProposal(
        id=row["id"], user_id=row["user_id"], plan=row["plan"] or {}, replace_week=bool(row["replace_week"]),
        status=row["status"], created_at=row["created_at"], delivered_at=row.get("delivered_at"),
        resolved_at=row.get("resolved_at"),
    )


def _row(row: dict) -> TrainingPlan:
    return TrainingPlan(
        user_id=row["user_id"],
        goal_id=row.get("goal_id"),
        baseline=row.get("baseline"),
        plan=row.get("plan") or {},
        updated_at=row["updated_at"],
    )


def _run_row(row: dict, tz) -> RunLog:
    """A logbook view over an activity row: note = the Garmin name plus the
    user's words, exactly the shape every consumer has always read. Dates are
    the USER's local date — a 00:30 run belongs to that day, not UTC's."""
    from datetime import datetime

    epoch = row.get("start_time_epoch_seconds")
    ran_on = datetime.fromtimestamp(epoch, tz=tz).date() if epoch else None
    note = (row.get("name") or "").strip()
    if row.get("average_heart_rate"):
        note += f" (avg HR {row['average_heart_rate']})"
    if row.get("user_note"):
        note = f"{note} — {row['user_note']}" if note else row["user_note"]
    # numeric columns arrive as Decimal — cast before any float math downstream
    meters = float(row["distance_meters"]) if row.get("distance_meters") else None
    return RunLog(
        id=row["id"],
        user_id=row.get("user_id"),
        ran_on=ran_on,
        note=note,
        distance_km=round(meters / 1000, 2) if meters else None,
        garmin_activity_id=row.get("activity_id"),
        created_at=row.get("updated_at"),
        activity_type=row.get("activity_type"),
        user_note=row.get("user_note"),
        name=row.get("name"),
        duration_min=(
            round(float(row["duration_milliseconds"]) / 60000, 1)
            if row.get("duration_milliseconds") else None
        ),
        avg_hr=row.get("average_heart_rate"),
        max_hr=row.get("maximum_heart_rate"),
    )
