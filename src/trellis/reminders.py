from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from trellis.tasks import Task, TaskStatus


class ReminderStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ReminderIntent:
    id: UUID
    user_id: UUID
    remind_at: datetime
    task_title: str  # task title or standalone label
    task_id: UUID | None = None
    status: ReminderStatus = ReminderStatus.SCHEDULED
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)


class ReminderRepository(Protocol):
    def schedule(self, reminder: ReminderIntent) -> ReminderIntent: ...

    def list_scheduled(self, user_id: UUID) -> list[ReminderIntent]: ...

    def due_between(
        self,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> list[ReminderIntent]: ...

    def cancel(self, reminder_id: UUID) -> ReminderIntent: ...

    def mark_sent(self, reminder_id: UUID) -> ReminderIntent: ...


class ReminderSchedulingError(ValueError):
    pass


class ReminderService:
    def __init__(self, repository: ReminderRepository):
        self.repository = repository

    def schedule_task_reminder(
        self,
        user_id: UUID,
        task: Task,
        remind_at: datetime,
        *,
        now: datetime | None = None,
    ) -> ReminderIntent:
        now = now or datetime.now(timezone.utc)
        if remind_at <= now:
            raise ReminderSchedulingError("Reminder time must be in the future")
        if task.user_id != user_id:
            raise ReminderSchedulingError("Task does not belong to user")
        if task.status not in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS):
            raise ReminderSchedulingError("Only open tasks can receive reminders")

        return self.repository.schedule(
            ReminderIntent(
                id=uuid4(),
                user_id=user_id,
                task_id=task.id,
                task_title=task.title,
                remind_at=remind_at,
                created_at=now,
            )
        )

    def schedule_standalone_reminder(
        self,
        user_id: UUID,
        label: str,
        remind_at: datetime,
        *,
        now: datetime | None = None,
    ) -> ReminderIntent:
        now = now or datetime.now(timezone.utc)
        if remind_at <= now:
            raise ReminderSchedulingError("Reminder time must be in the future")
        return self.repository.schedule(
            ReminderIntent(
                id=uuid4(),
                user_id=user_id,
                task_id=None,
                task_title=label,
                remind_at=remind_at,
                created_at=now,
            )
        )

    def due_between(
        self,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> list[ReminderIntent]:
        if end_at < start_at:
            raise ValueError("end_at must be after start_at")
        return self.repository.due_between(user_id, start_at, end_at)

    def list_scheduled(self, user_id: UUID) -> list[ReminderIntent]:
        return self.repository.list_scheduled(user_id)

    def cancel(self, reminder_id: UUID) -> ReminderIntent:
        return self.repository.cancel(reminder_id)

    def mark_sent(self, reminder_id: UUID) -> ReminderIntent:
        return self.repository.mark_sent(reminder_id)


class InMemoryReminderRepository:
    def __init__(self):
        self.reminders: dict[UUID, ReminderIntent] = {}

    def schedule(self, reminder: ReminderIntent) -> ReminderIntent:
        self.reminders[reminder.id] = reminder
        return reminder

    def due_between(
        self,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> list[ReminderIntent]:
        return sorted(
            (
                reminder
                for reminder in self.reminders.values()
                if reminder.user_id == user_id
                and reminder.status == ReminderStatus.SCHEDULED
                and start_at <= reminder.remind_at <= end_at
            ),
            key=lambda reminder: reminder.remind_at,
        )

    def list_scheduled(self, user_id: UUID) -> list[ReminderIntent]:
        return sorted(
            (
                reminder
                for reminder in self.reminders.values()
                if reminder.user_id == user_id
                and reminder.status == ReminderStatus.SCHEDULED
            ),
            key=lambda reminder: reminder.remind_at,
        )

    def cancel(self, reminder_id: UUID) -> ReminderIntent:
        reminder = self.reminders[reminder_id]
        cancelled = replace(reminder, status=ReminderStatus.CANCELLED)
        self.reminders[reminder_id] = cancelled
        return cancelled

    def mark_sent(self, reminder_id: UUID) -> ReminderIntent:
        reminder = self.reminders[reminder_id]
        sent = replace(reminder, status=ReminderStatus.SENT)
        self.reminders[reminder_id] = sent
        return sent
