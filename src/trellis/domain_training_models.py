"""
Training model — one lean record per user. The coach (Claude) reads the goal +
baseline + real calendar and authors the plan JSON in conversation; this only
carries it. No enums, no session/plan-status machinery — that judgment is Claude's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class TrainingPlan:
    user_id: UUID
    goal_id: UUID | None
    baseline: str | None
    plan: dict[str, Any]          # Claude-authored: {"arc": "...", "week": [{date,type,detail}, ...]}
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class RunLog:
    """A completed run the coach records — so it plans the next from the last.
    Lean: the date, what happened in plain words, and optional distance/feel."""
    id: UUID
    user_id: UUID
    ran_on: date
    note: str                     # "easy 5k, felt strong" — the coach's/user's words
    distance_km: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
