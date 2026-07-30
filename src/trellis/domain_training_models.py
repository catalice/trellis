"""
Training domain models — frozen dataclasses only. No I/O, no imports from other
trellis modules. A TrainingPlan is Claude's periodisation (the arc) as stored
data; PlannedSessions are the dated runs it schedules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import UUID


class SessionType(StrEnum):
    EASY = "easy"           # conversational aerobic run
    LONG = "long"           # the week's endurance run
    INTERVALS = "intervals" # hard reps with recovery
    TEMPO = "tempo"         # sustained comfortably-hard effort
    RECOVERY = "recovery"   # very easy shakeout
    REST = "rest"           # deliberate no-run day


class SessionStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    SKIPPED = "skipped"


class PlanStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class PlannedSession:
    id: UUID
    plan_id: UUID
    user_id: UUID
    scheduled_date: date
    session_type: SessionType
    description: str
    planned_distance_km: float | None = None
    planned_duration_min: int | None = None
    status: SessionStatus = SessionStatus.PLANNED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_run(self) -> bool:
        return self.session_type not in (SessionType.REST,)


@dataclass(frozen=True)
class TrainingPlan:
    id: UUID
    user_id: UUID
    goal_id: UUID | None
    goal_snapshot: str          # goal state at build time — for later staleness checks
    rationale: str              # Claude's one-line 'shape of this block'
    status: PlanStatus = PlanStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_stale(self, current_goal_snapshot: str) -> bool:
        """True when the goal has moved since this plan was built — the hook a
        later slice uses to offer a re-plan. Compared on the stored snapshot."""
        return self.goal_snapshot != current_goal_snapshot
