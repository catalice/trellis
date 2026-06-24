"""
Training domain storage — protocols + Postgres implementations.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from trellis.training_models import WeeklyPlan, ArcPhase, ArcRepository, TrainingArc
from trellis.training_postgres import PostgresTrainingRepository
from trellis.postgres import PostgresArcRepository, PostgresTrainingAnchorRepository
from trellis.user_context import AnchorService, TrainingAnchor, TrainingAnchorRepository


class TrainingPlanRepository(Protocol):
    def save_active(self, user_id: UUID, plan: WeeklyPlan) -> WeeklyPlan: ...
    def latest_active(self, user_id: UUID, week_start: date) -> WeeklyPlan | None: ...


__all__ = [
    "TrainingPlanRepository",
    "ArcRepository",
    "TrainingAnchorRepository",
    "PostgresTrainingRepository",
    "PostgresArcRepository",
    "PostgresTrainingAnchorRepository",
    "AnchorService",
    "TrainingAnchor",
]
