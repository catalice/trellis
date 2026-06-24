from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.captures import Idea
from trellis.tasks import Task


@dataclass(frozen=True)
class CorrectionPlan:
    action: str
    task_ids: tuple[UUID, ...]
    target_idea_id: UUID | None
    idea_title: str
    idea_synthesis: str
    summary: str
    idea_ids: tuple[UUID, ...] = ()
    task_title: str = ""


@dataclass(frozen=True)
class CorrectionResult:
    action: str
    summary: str
    created_at: datetime
    idea: Idea | None = None
    archived_tasks: tuple[Task, ...] = ()
    task: Task | None = None
    archived_ideas: tuple[Idea, ...] = ()


class CorrectionInterpreter(Protocol):
    def interpret_correction(
        self,
        instruction: str,
        tasks: list[Task],
        ideas: list[Idea],
        now: datetime,
    ) -> CorrectionPlan: ...


class CorrectionRepository(Protocol):
    def apply_task_to_idea(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult: ...

    def apply_idea_to_task(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult: ...

    def apply_rename_task(
        self,
        user_id: UUID,
        instruction: str,
        plan: CorrectionPlan,
    ) -> CorrectionResult: ...


