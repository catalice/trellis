from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

# Sentinel used by update_task to distinguish "not provided" from None (clear the value).
class _UnsetType:
    __slots__ = ()
    def __repr__(self) -> str:
        return "UNSET"

UNSET: _UnsetType = _UnsetType()


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"
    ARCHIVED = "archived"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Energy(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Task:
    id: UUID
    user_id: UUID
    title: str
    status: TaskStatus = TaskStatus.OPEN
    priority: Priority = Priority.MEDIUM
    energy: Energy = Energy.MEDIUM
    due_at: datetime | None = None
    source_capture_id: UUID | None = None
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    completed_at: datetime | None = None


class TaskRepository(Protocol):
    def create(self, task: Task) -> Task: ...

    def list_open(self, user_id: UUID) -> list[Task]: ...

    def find_open_by_title(self, user_id: UUID, title: str) -> list[Task]: ...

    def complete(self, task_id: UUID, completed_at: datetime) -> Task: ...

    def archive(self, task_id: UUID) -> Task: ...

    def update_task(
        self,
        task_id: UUID,
        *,
        new_title: str | None = None,
        due_at: datetime | None | _UnsetType = UNSET,
        priority: str | None = None,
        energy: str | None = None,
    ) -> Task: ...

    def list_completed(self, user_id: UUID, limit: int) -> list[Task]: ...


class TaskProjection(Protocol):
    def write(self, tasks: list[Task]) -> None: ...


class TaskNotFoundError(LookupError):
    pass


class DuplicateTaskError(ValueError):
    def __init__(self, task: Task):
        self.task = task
        super().__init__(f"Task already exists: {task.title}")


class AmbiguousTaskError(LookupError):
    def __init__(self, matches: list[Task]):
        self.matches = matches
        super().__init__("More than one open task matched")


class TaskService:
    def __init__(self, repository: TaskRepository, projection: TaskProjection):
        self.repository = repository
        self.projection = projection

    def create(
        self,
        user_id: UUID,
        title: str,
        *,
        priority: Priority = Priority.MEDIUM,
        energy: Energy = Energy.MEDIUM,
        due_at: datetime | None = None,
        source_capture_id: UUID | None = None,
    ) -> Task:
        clean_title = " ".join(title.split()).strip(" .")
        if not clean_title:
            raise ValueError("Task title cannot be empty")
        existing = self.repository.find_open_by_title(user_id, clean_title)
        exact = [
            task
            for task in existing
            if self._normalize(task.title) == self._normalize(clean_title)
        ]
        if exact:
            raise DuplicateTaskError(exact[0])

        now = datetime.now(timezone.utc)
        task = Task(
            id=uuid4(),
            user_id=user_id,
            title=clean_title,
            priority=priority,
            energy=energy,
            due_at=due_at,
            source_capture_id=source_capture_id,
            created_at=now,
        )
        created = self.repository.create(task)
        self._project(user_id)
        return created

    def create_many(
        self,
        user_id: UUID,
        titles: tuple[str, ...],
        *,
        source_capture_id: UUID | None = None,
    ) -> tuple[list[Task], list[Task]]:
        created: list[Task] = []
        existing: list[Task] = []
        for title in titles:
            try:
                created.append(
                    self.create(
                        user_id,
                        title,
                        source_capture_id=source_capture_id,
                    )
                )
            except DuplicateTaskError as error:
                existing.append(error.task)
        return created, existing

    def complete(self, user_id: UUID, title: str) -> Task:
        matches = self.repository.find_open_by_title(user_id, title)
        if not matches:
            raise TaskNotFoundError(title)
        if len(matches) > 1:
            raise AmbiguousTaskError(matches)

        completed = self.repository.complete(matches[0].id, datetime.now(timezone.utc))
        self._project(user_id)
        return completed

    def archive(self, user_id: UUID, reference: str) -> list[Task]:
        matches = self._match_archive_reference(user_id, reference)
        archived = [self.repository.archive(task.id) for task in matches]
        self._project(user_id)
        return archived

    def update_task(
        self,
        user_id: UUID,
        reference: str,
        *,
        new_title: str | None = None,
        due_at: datetime | None | _UnsetType = UNSET,
        priority: str | None = None,
        energy: str | None = None,
    ) -> Task:
        matches = self.repository.find_open_by_title(user_id, reference)
        if not matches:
            raise TaskNotFoundError(reference)
        if len(matches) > 1:
            raise AmbiguousTaskError(matches)
        if new_title is not None:
            new_title = " ".join(new_title.split()).strip(" .")
            if not new_title:
                raise ValueError("Task title cannot be empty")
        if priority is not None and priority not in ("low", "medium", "high"):
            raise ValueError(f"Invalid priority: {priority!r}")
        if energy is not None and energy not in ("low", "medium", "high"):
            raise ValueError(f"Invalid energy: {energy!r}")
        updated = self.repository.update_task(
            matches[0].id,
            new_title=new_title,
            due_at=due_at,
            priority=priority,
            energy=energy,
        )
        self._project(user_id)
        return updated

    def list_open(self, user_id: UUID) -> list[Task]:
        return self.repository.list_open(user_id)

    def list_completed(self, user_id: UUID, limit: int = 20) -> list[Task]:
        return self.repository.list_completed(user_id, limit)

    def select_today(
        self,
        user_id: UUID,
        *,
        energy: Energy | None = None,
        limit: int = 3,
    ) -> list[Task]:
        now = datetime.now(timezone.utc)
        priority_weight = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}

        def score(task: Task) -> tuple:
            overdue_rank = 0 if task.due_at and task.due_at <= now else 1
            due_rank = task.due_at or datetime.max.replace(tzinfo=timezone.utc)
            energy_rank = 0 if energy is None or task.energy == energy else 1
            return overdue_rank, due_rank, priority_weight[task.priority], energy_rank, task.created_at

        return sorted(self.repository.list_open(user_id), key=score)[:limit]

    def _project(self, user_id: UUID) -> None:
        self.projection.write(self.repository.list_open(user_id))

    def _match_archive_reference(self, user_id: UUID, reference: str) -> list[Task]:
        clean = " ".join(reference.split()).strip(" .")
        if not clean:
            raise TaskNotFoundError(reference)

        number_matches = [
            int(match)
            for match in re.findall(r"\b\d+\b", clean)
        ]
        if number_matches:
            tasks = self.repository.list_open(user_id)
            selected: list[Task] = []
            for number in number_matches:
                index = number - 1
                if index < 0 or index >= len(tasks):
                    raise TaskNotFoundError(str(number))
                task = tasks[index]
                if task not in selected:
                    selected.append(task)
            return selected

        matches = self.repository.find_open_by_title(user_id, clean)
        if not matches:
            raise TaskNotFoundError(clean)
        if len(matches) > 1:
            raise AmbiguousTaskError(matches)
        return matches

    @staticmethod
    def _normalize(title: str) -> str:
        return " ".join(title.casefold().split()).strip(" .")


class InMemoryTaskRepository:
    def __init__(self):
        self.tasks: dict[UUID, Task] = {}

    def create(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def list_open(self, user_id: UUID) -> list[Task]:
        return [
            task
            for task in self.tasks.values()
            if task.user_id == user_id
            and task.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)
        ]

    def find_open_by_title(self, user_id: UUID, title: str) -> list[Task]:
        query = " ".join(title.lower().split())
        exact = [
            task
            for task in self.list_open(user_id)
            if task.title.lower() == query
        ]
        if exact:
            return exact
        return [
            task
            for task in self.list_open(user_id)
            if query in task.title.lower()
        ]

    def complete(self, task_id: UUID, completed_at: datetime) -> Task:
        task = self.tasks[task_id]
        completed = replace(
            task,
            status=TaskStatus.DONE,
            completed_at=completed_at,
        )
        self.tasks[task_id] = completed
        return completed

    def archive(self, task_id: UUID) -> Task:
        task = self.tasks[task_id]
        archived = replace(task, status=TaskStatus.ARCHIVED)
        self.tasks[task_id] = archived
        return archived

    def update_task(
        self,
        task_id: UUID,
        *,
        new_title: str | None = None,
        due_at: datetime | None | _UnsetType = UNSET,
        priority: str | None = None,
        energy: str | None = None,
    ) -> Task:
        task = self.tasks[task_id]
        kwargs: dict = {}
        if new_title is not None:
            kwargs["title"] = new_title
        if not isinstance(due_at, _UnsetType):
            kwargs["due_at"] = due_at
        if priority is not None:
            kwargs["priority"] = Priority(priority)
        if energy is not None:
            kwargs["energy"] = Energy(energy)
        updated = replace(task, **kwargs)
        self.tasks[task_id] = updated
        return updated

    def list_completed(self, user_id: UUID, limit: int = 20) -> list[Task]:
        return sorted(
            (t for t in self.tasks.values() if t.user_id == user_id and t.status == TaskStatus.DONE),
            key=lambda t: t.completed_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:limit]
