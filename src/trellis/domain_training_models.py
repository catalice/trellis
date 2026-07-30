"""
Training model — one lean record per user. The coach (Claude) reads the goal +
baseline + real calendar and authors the plan JSON in conversation; this only
carries it. No enums, no session/plan-status machinery — that judgment is Claude's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class TrainingPlan:
    user_id: UUID
    goal_id: UUID | None
    baseline: str | None
    plan: dict[str, Any]          # Claude-authored: {"arc": "...", "week": [{date,type,detail}, ...]}
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
