from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol
from uuid import UUID

from trellis.tasks import TaskService


@dataclass(frozen=True)
class Capture:
    id: UUID
    user_id: UUID
    content: str
    created_at: datetime
    synthesis: str | None = None
    observations: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Idea:
    id: UUID
    user_id: UUID
    title: str
    synthesis: str
    created_at: datetime
    source_capture_id: UUID | None = None


@dataclass(frozen=True)
class Interpretation:
    synthesis: str
    tasks: tuple[str, ...]
    ideas: tuple[tuple[str, str], ...]
    observations: tuple[str, ...]
    questions: tuple[str, ...]
    decisions: tuple[str, ...]


@dataclass(frozen=True)
class CaptureResult:
    capture: Capture
    tasks_created: int
    tasks_existing: int
    ideas_created: int
    ideas_existing: int


class CaptureRepository(Protocol):
    def create_pending(self, user_id: UUID, content: str) -> Capture: ...

    def mark_processed(
        self,
        capture_id: UUID,
        interpretation: Interpretation,
    ) -> Capture: ...

    def mark_failed(self, capture_id: UUID, error: str) -> None: ...

    def save(self, user_id: UUID, raw: str, synthesis: str) -> Capture: ...

    def list_recent(self, user_id: UUID, limit: int) -> list[Capture]: ...

    def search_recent(self, user_id: UUID, reference: str, limit: int) -> list[Capture]: ...


class IdeaRepository(Protocol):
    def create_or_get(
        self,
        user_id: UUID,
        title: str,
        synthesis: str,
        source_capture_id: UUID,
    ) -> tuple[Idea, bool]: ...

    def list_inbox(self, user_id: UUID) -> list[Idea]: ...


class Interpreter(Protocol):
    def interpret(self, text: str, now: datetime) -> Interpretation: ...


class CaptureProjection(Protocol):
    def write(
        self,
        capture: Capture,
        *,
        task_titles: list[str],
        idea_titles: list[str],
    ) -> None: ...


class IdeaProjection(Protocol):
    def write(self, ideas: list[Idea]) -> None: ...


class CaptureService:
    def __init__(
        self,
        captures: CaptureRepository,
        ideas: IdeaRepository,
        tasks: TaskService,
        interpreter: Interpreter,
        capture_projection: CaptureProjection,
        idea_projection: IdeaProjection,
        timezone: tzinfo,
    ):
        self.captures = captures
        self.ideas = ideas
        self.tasks = tasks
        self.interpreter = interpreter
        self.capture_projection = capture_projection
        self.idea_projection = idea_projection
        self.timezone = timezone

    def process(self, user_id: UUID, text: str) -> CaptureResult:
        clean = text.strip()
        if not clean:
            raise ValueError("Capture cannot be empty")

        pending = self.captures.create_pending(user_id, text)
        try:
            interpretation = self.interpreter.interpret(
                text,
                pending.created_at.astimezone(self.timezone),
            )
            created_tasks, existing_tasks = self.tasks.create_many(
                user_id,
                interpretation.tasks,
                source_capture_id=pending.id,
            )

            created_ideas: list[Idea] = []
            existing_ideas: list[Idea] = []
            for title, synthesis in interpretation.ideas:
                idea, created = self.ideas.create_or_get(
                    user_id,
                    title,
                    synthesis,
                    pending.id,
                )
                (created_ideas if created else existing_ideas).append(idea)

            capture = self.captures.mark_processed(pending.id, interpretation)
            self.capture_projection.write(
                capture,
                task_titles=[task.title for task in created_tasks],
                idea_titles=[idea.title for idea in created_ideas],
            )
            self.idea_projection.write(self.ideas.list_inbox(user_id))
            return CaptureResult(
                capture=capture,
                tasks_created=len(created_tasks),
                tasks_existing=len(existing_tasks),
                ideas_created=len(created_ideas),
                ideas_existing=len(existing_ideas),
            )
        except Exception as error:
            self.captures.mark_failed(pending.id, str(error))
            self.capture_projection.write(
                pending,
                task_titles=[],
                idea_titles=[],
            )
            raise
