from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Goal:
    id: UUID
    user_id: UUID
    title: str
    goal_type: str  # 'race' | 'aerobic' | 'strength' | 'general'
    status: str = "active"  # 'active' | 'achieved' | 'paused' | 'dropped'
    target_date: date | None = None
    is_fixed_date: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        parts = [f"{self.goal_type.capitalize()}: {self.title}"]
        if self.target_date:
            fixed = " (fixed)" if self.is_fixed_date else ""
            parts.append(f"target {self.target_date.isoformat()}{fixed}")
        if self.metrics:
            metric_parts = [f"{k}: {v}" for k, v in self.metrics.items()]
            parts.append(", ".join(metric_parts))
        if self.notes:
            parts.append(self.notes)
        return " — ".join(parts)


class GoalRepository(Protocol):
    def create(self, goal: Goal) -> Goal: ...
    def list_active(self, user_id: UUID) -> list[Goal]: ...
    def update(self, goal_id: UUID, **kwargs) -> Goal: ...
    def get(self, goal_id: UUID) -> Goal | None: ...


class GoalNotFoundError(Exception):
    pass


class GoalService:
    def __init__(self, repository: GoalRepository) -> None:
        self.repository = repository

    def add(
        self,
        user_id: UUID,
        title: str,
        goal_type: str,
        *,
        target_date: date | None = None,
        is_fixed_date: bool = False,
        metrics: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> Goal:
        goal = Goal(
            id=uuid4(),
            user_id=user_id,
            title=title,
            goal_type=goal_type,
            target_date=target_date,
            is_fixed_date=is_fixed_date,
            metrics=metrics or {},
            notes=notes,
        )
        return self.repository.create(goal)

    def list_active(self, user_id: UUID) -> list[Goal]:
        return self.repository.list_active(user_id)

    def achieve(self, user_id: UUID, goal_id: UUID) -> Goal:
        goal = self.repository.get(goal_id)
        if goal is None or goal.user_id != user_id:
            raise GoalNotFoundError(goal_id)
        return self.repository.update(goal_id, status="achieved")

    def update(
        self,
        user_id: UUID,
        goal_id: UUID,
        *,
        title: str | None = None,
        target_date: date | None = None,
        is_fixed_date: bool | None = None,
        metrics: dict[str, Any] | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> Goal:
        goal = self.repository.get(goal_id)
        if goal is None or goal.user_id != user_id:
            raise GoalNotFoundError(goal_id)
        kwargs = {}
        if title is not None:
            kwargs["title"] = title
        if target_date is not None:
            kwargs["target_date"] = target_date
        if is_fixed_date is not None:
            kwargs["is_fixed_date"] = is_fixed_date
        if metrics is not None:
            kwargs["metrics"] = metrics
        if notes is not None:
            kwargs["notes"] = notes
        if status is not None:
            kwargs["status"] = status
        return self.repository.update(goal_id, **kwargs)

    def format_for_context(self, user_id: UUID) -> str | None:
        goals = self.list_active(user_id)
        if not goals:
            return None
        lines = ["Active goals:"]
        for g in goals:
            lines.append(f"  - {g.summary()}")
        return "\n".join(lines)
