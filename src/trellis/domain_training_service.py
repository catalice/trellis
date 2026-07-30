"""
Training service — thin. Reads the goal from the second brain, asks the Claude
layer to design the arc, stores the plan + sessions, and answers "this week" /
"today". Returns typed data only — string formatting belongs to the tool handler.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, tzinfo
from typing import Protocol
from uuid import UUID, uuid4

from trellis.domain_training_claude import TrainingClaude
from trellis.domain_training_models import (
    PlannedSession,
    PlanStatus,
    SessionStatus,
    TrainingPlan,
)
from trellis.domain_training_repo import TrainingRepository

_log = logging.getLogger(__name__)

_DEFAULT_DAYS_PER_WEEK = 4
_PLAN_HORIZON_DAYS = 14


class GoalReader(Protocol):
    """The training-relevant goals, read from the second brain — training never
    stores its own goals."""
    def list_training_goals(self, user_id: UUID) -> list: ...


class TrainingNotConnectedError(Exception):
    """No training goal set yet — nothing to build a plan from."""


class TrainingService:
    def __init__(
        self,
        repo: TrainingRepository,
        goals: GoalReader,
        claude: TrainingClaude,
        tz: tzinfo,
    ) -> None:
        self._repo = repo
        self._goals = goals
        self._claude = claude
        self._tz = tz

    # -- build ----------------------------------------------------------------

    def build_plan_from_goal(
        self, user_id: UUID, *, now: datetime, days_per_week: int = _DEFAULT_DAYS_PER_WEEK
    ) -> TrainingPlan | None:
        """Generate and store a fresh plan from the user's training goal(s).
        Returns None if Claude couldn't produce a usable plan. Raises
        TrainingNotConnectedError if there's no training goal to build from."""
        goals = self._goals.list_training_goals(user_id)
        if not goals:
            raise TrainingNotConnectedError()

        goals_summary = "\n".join(f"- {g.summary()}" for g in goals)
        snapshot = _goal_snapshot(goals)
        anchor_goal_id = goals[0].id

        local = now.astimezone(self._tz)
        today_line = local.strftime("%A %d %B %Y")

        generated = self._claude.generate_plan(
            goals_summary=goals_summary,
            today_line=today_line,
            days_per_week=max(1, min(7, days_per_week)),
        )
        if generated is None:
            return None

        self._repo.supersede_active(user_id)
        plan = self._repo.save_plan(TrainingPlan(
            id=uuid4(),
            user_id=user_id,
            goal_id=anchor_goal_id,
            goal_snapshot=snapshot,
            rationale=generated.rationale,
            status=PlanStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        ))
        self._repo.save_sessions([
            PlannedSession(
                id=uuid4(),
                plan_id=plan.id,
                user_id=user_id,
                scheduled_date=gs.scheduled_date,
                session_type=gs.session_type,
                description=gs.description,
                planned_distance_km=gs.distance_km,
                planned_duration_min=gs.duration_min,
                status=SessionStatus.PLANNED,
                created_at=now,
            )
            for gs in generated.sessions
        ])
        return plan

    # -- read -----------------------------------------------------------------

    def active_plan(self, user_id: UUID) -> TrainingPlan | None:
        return self._repo.get_active_plan(user_id)

    def is_plan_stale(self, user_id: UUID) -> bool:
        """True when the goal has moved since the active plan was built (the hook
        a later slice turns into a re-plan prompt)."""
        plan = self._repo.get_active_plan(user_id)
        if plan is None:
            return False
        return plan.is_stale(_goal_snapshot(self._goals.list_training_goals(user_id)))

    def this_week(self, user_id: UUID, now: datetime) -> list[PlannedSession]:
        start, end = _week_bounds(now.astimezone(self._tz).date())
        return self._repo.list_sessions(user_id, start=start, end=end)

    def upcoming(self, user_id: UUID, now: datetime, *, days: int = _PLAN_HORIZON_DAYS) -> list[PlannedSession]:
        today = now.astimezone(self._tz).date()
        return self._repo.list_sessions(user_id, start=today, end=today + timedelta(days=days))

    def todays_session(self, user_id: UUID, now: datetime) -> PlannedSession | None:
        return self._repo.session_on(user_id, now.astimezone(self._tz).date())

    def mark_session(
        self, user_id: UUID, session_id: UUID, status: SessionStatus
    ) -> PlannedSession:
        session = self._repo.get_session(session_id)
        if session is None or session.user_id != user_id:
            raise LookupError(session_id)
        return self._repo.update_session_status(session_id, status)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_bounds(today: date) -> tuple[date, date]:
    """Monday-anchored week containing `today`."""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def _goal_snapshot(goals: list) -> str:
    """A stable string of the training goals' identity + state, for staleness
    detection: if any goal is added, removed, or edited, this changes."""
    return "|".join(
        f"{g.id}:{g.updated_at.isoformat()}:{g.summary()}"
        for g in sorted(goals, key=lambda g: str(g.id))
    )
