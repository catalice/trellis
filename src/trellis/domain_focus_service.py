from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.domain_focus_models import (
    Capture,
    CaptureType,
    CleanupAssignment,
    CleanupSummary,
    Effort,
    EffortIntensity,
    Goal,
    GoalStatus,
    GoalType,
    Reminder,
    Task,
    TaskEnergy,
    TaskEvent,
    TaskKind,
    TaskPriority,
    TaskStatus,
    BrainDumpResult,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

class CaptureRepository(Protocol):
    def save(self, capture: Capture) -> Capture: ...
    def get(self, capture_id: UUID) -> Capture | None: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[Capture]: ...
    def list_unassigned(self, user_id: UUID, *, since: date) -> list[Capture]: ...
    def assign_to_effort(self, capture_id: UUID, effort_id: UUID | None) -> Capture: ...
    def archive(self, capture_id: UUID) -> None: ...
    def delete(self, user_id: UUID, capture_id: UUID) -> bool: ...


class EffortRepository(Protocol):
    def save(self, effort: Effort) -> Effort: ...
    def get(self, effort_id: UUID) -> Effort | None: ...
    def get_by_title(self, user_id: UUID, title: str) -> Effort | None: ...
    def list_all(self, user_id: UUID) -> list[Effort]: ...
    def update_intensity(self, effort_id: UUID, intensity: EffortIntensity) -> Effort: ...
    def update_notes(self, effort_id: UUID, notes: str) -> Effort: ...


class TaskRepository(Protocol):
    def save(self, task: Task) -> Task: ...
    def get(self, task_id: UUID) -> Task | None: ...
    def list_open(self, user_id: UUID) -> list[Task]: ...
    def list_parked(self, user_id: UUID) -> list[Task]: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[Task]: ...
    def update(self, task_id: UUID, **kwargs: Any) -> Task: ...
    def delete(self, user_id: UUID, task_id: UUID) -> bool: ...
    def save_event(self, event: TaskEvent) -> None: ...


class ReminderRepository(Protocol):
    def save(self, reminder: Reminder) -> Reminder: ...
    def get(self, reminder_id: UUID) -> Reminder | None: ...
    def list_upcoming(self, user_id: UUID, *, before: datetime) -> list[Reminder]: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[Reminder]: ...
    def cancel(self, reminder_id: UUID) -> None: ...
    def mark_sent(self, reminder_id: UUID) -> None: ...


class GoalRepository(Protocol):
    def save(self, goal: Goal) -> Goal: ...
    def get(self, goal_id: UUID) -> Goal | None: ...
    def list_active(self, user_id: UUID) -> list[Goal]: ...
    def update(self, goal_id: UUID, **kwargs: Any) -> Goal: ...


class BrainDumpClaude(Protocol):
    def synthesise(self, raw_text: str, current_date_line: str) -> BrainDumpResult | None: ...
    def suggest_efforts(self, capture_summaries: list[str]) -> list: ...


class VaultProjection(Protocol):
    """Write-only view of the second brain (Obsidian). Implementations must
    never raise — a failed vault write must not break the bot."""
    def capture_saved(self, capture: Capture, tasks: tuple[Task, ...] = ()) -> None: ...
    def tasks_changed(self, user_id: UUID) -> None: ...
    def effort_created(self, effort: Effort) -> None: ...
    def capture_assigned(self, capture: Capture) -> None: ...
    def research_saved(self, capture: Capture) -> None: ...


class Memory(Protocol):
    """Semantic memory index (see infra_memory). Optional: when absent, writes
    still succeed unindexed and recall is unavailable. remember/forget never raise
    — indexing a row must never break the row's own write."""
    def remember(self, user_id: UUID, entity_kind: str, entity_id: UUID, text: str) -> None: ...
    def forget(self, entity_kind: str, entity_id: UUID) -> None: ...


# ---------------------------------------------------------------------------
# Result types (service-layer only — not stored)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessedDump:
    capture: Capture
    tasks_created: tuple[Task, ...]
    synthesis: BrainDumpResult | None     # None if Claude call failed; capture still saved


class TaskNotFoundError(Exception):
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id
        super().__init__(str(task_id))


class GoalNotFoundError(Exception):
    def __init__(self, goal_id: UUID) -> None:
        self.goal_id = goal_id
        super().__init__(str(goal_id))


# ---------------------------------------------------------------------------
# BrainDumpService
# ---------------------------------------------------------------------------

class BrainDumpService:
    def __init__(
        self,
        capture_repo: CaptureRepository,
        task_repo: TaskRepository,
        claude: BrainDumpClaude,
        timezone: tzinfo,
        projection: VaultProjection | None = None,
        memory: Memory | None = None,
    ) -> None:
        self._captures = capture_repo
        self._tasks = task_repo
        self._claude = claude
        self._tz = timezone
        self._projection = projection
        self._memory = memory

    def process(self, user_id: UUID, raw: str, now: datetime) -> ProcessedDump:
        local = now.astimezone(self._tz)
        date_line = local.strftime("%A %d %B %Y, %H:%M") + f" ({self._tz})"
        result = self._claude.synthesise(raw, date_line)

        capture = self._captures.save(Capture(
            id=uuid4(),
            user_id=user_id,
            raw=raw,
            capture_type=result.capture_type if result else CaptureType.BRAIN_DUMP,
            synthesis=result.cleaned_text if result else None,
            summary=result.summary if result else None,
            effort_id=None,
            created_at=now,
        ))

        if self._memory is not None:
            self._memory.remember(user_id, "capture", capture.id, capture.embedding_text())

        tasks: list[Task] = []
        if result:
            for extracted in result.extracted_tasks:
                due_at = _parse_local_due(extracted.due, self._tz)
                task = self._tasks.save(Task(
                    id=uuid4(),
                    user_id=user_id,
                    title=extracted.title,
                    status=TaskStatus.OPEN,
                    priority=extracted.priority,
                    energy=extracted.energy,
                    kind=extracted.kind,
                    due_at=due_at,
                    source_capture_id=capture.id,
                    created_at=now,
                    updated_at=now,
                ))
                tasks.append(task)

        if self._projection:
            self._projection.capture_saved(capture, tuple(tasks))
            if tasks:
                self._projection.tasks_changed(user_id)

        return ProcessedDump(capture=capture, tasks_created=tuple(tasks), synthesis=result)


# ---------------------------------------------------------------------------
# CaptureService
# ---------------------------------------------------------------------------

class CaptureService:
    def __init__(
        self,
        repo: CaptureRepository,
        projection: VaultProjection | None = None,
        memory: Memory | None = None,
    ) -> None:
        self._repo = repo
        self._projection = projection
        self._memory = memory

    def list_recent(self, user_id: UUID, *, limit: int = 20) -> list[Capture]:
        return self._repo.list_recent(user_id, limit=limit)

    def list_unassigned(self, user_id: UUID, *, days: int = 30) -> list[Capture]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        return self._repo.list_unassigned(user_id, since=since)

    def assign(self, capture_id: UUID, effort_id: UUID) -> Capture:
        capture = self._repo.assign_to_effort(capture_id, effort_id)
        if self._projection:
            self._projection.capture_assigned(capture)
        return capture

    def archive(self, capture_id: UUID) -> None:
        self._repo.archive(capture_id)
        # Archived captures leave the inbox — drop them from recall too so it
        # never surfaces something the user has consciously filed away.
        if self._memory is not None:
            self._memory.forget("capture", capture_id)

    def delete(self, user_id: UUID, capture_id: UUID) -> bool:
        """Erase a mis-capture (test, mistake) — tasks extracted from it are
        erased separately via their own ids; the FK just nulls their source."""
        deleted = self._repo.delete(user_id, capture_id)
        if deleted and self._memory is not None:
            self._memory.forget("capture", capture_id)
        return deleted

    def save_research(self, user_id: UUID, content: str, *, effort_id: UUID, now: datetime) -> Capture:
        """Store a piece of research/notes onto an effort. Full text lands on
        the effort's vault page; a one-line receipt lands in the day's log."""
        summary = content.strip().splitlines()[0][:80] if content.strip() else "research"
        capture = self._repo.save(Capture(
            id=uuid4(),
            user_id=user_id,
            raw=content,
            capture_type=CaptureType.REFERENCE,
            synthesis=content,
            summary=summary,
            effort_id=effort_id,
            created_at=now,
        ))
        if self._memory is not None:
            self._memory.remember(user_id, "capture", capture.id, capture.embedding_text())
        if self._projection:
            self._projection.research_saved(capture)
        return capture


# ---------------------------------------------------------------------------
# EffortService
# ---------------------------------------------------------------------------

class EffortService:
    def __init__(
        self,
        repo: EffortRepository,
        projection: VaultProjection | None = None,
        memory: Memory | None = None,
    ) -> None:
        self._repo = repo
        self._projection = projection
        self._memory = memory

    def _embed(self, effort: Effort) -> None:
        if self._memory is not None:
            self._memory.remember(effort.user_id, "effort", effort.id, effort.embedding_text())

    def create(
        self,
        user_id: UUID,
        title: str,
        intensity: EffortIntensity,
        notes: str | None = None,
    ) -> Effort:
        now = datetime.now(timezone.utc)
        effort = self._repo.save(Effort(
            id=uuid4(),
            user_id=user_id,
            title=title,
            intensity=intensity,
            notes=notes,
            obsidian_path=_effort_obsidian_path(title),
            created_at=now,
            updated_at=now,
        ))
        self._embed(effort)
        if self._projection:
            self._projection.effort_created(effort)
        return effort

    def find_or_create(self, user_id: UUID, title: str, now: datetime) -> Effort:
        """Return the effort with this title, creating it (active) if new.
        This is how a seed graduates: research it, and it gets a home."""
        existing = self._repo.get_by_title(user_id, title)
        if existing is not None:
            return existing
        effort = self._repo.save(Effort(
            id=uuid4(),
            user_id=user_id,
            title=title,
            intensity=EffortIntensity.ACTIVE,
            notes=None,
            obsidian_path=_effort_obsidian_path(title),
            created_at=now,
            updated_at=now,
        ))
        self._embed(effort)
        if self._projection:
            self._projection.effort_created(effort)
        return effort

    def list_all(self, user_id: UUID) -> list[Effort]:
        return self._repo.list_all(user_id)

    def list_active(self, user_id: UUID) -> list[Effort]:
        return [e for e in self._repo.list_all(user_id) if e.intensity == EffortIntensity.ACTIVE]

    def set_intensity(self, effort_id: UUID, intensity: EffortIntensity) -> Effort:
        return self._repo.update_intensity(effort_id, intensity)

    def add_notes(self, effort_id: UUID, notes: str) -> Effort:
        return self._repo.update_notes(effort_id, notes)

    def summary_for_context(self, user_id: UUID) -> str | None:
        efforts = self._repo.list_all(user_id)
        if not efforts:
            return None
        lines: list[str] = []
        for intensity in EffortIntensity:
            group = [e for e in efforts if e.intensity == intensity]
            if group:
                lines.append(f"{intensity.value.capitalize()}: " + ", ".join(e.title for e in group))
        return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# TaskService
# ---------------------------------------------------------------------------

class TaskService:
    def __init__(
        self,
        repo: TaskRepository,
        tz: tzinfo,
        projection: VaultProjection | None = None,
        memory: Memory | None = None,
    ) -> None:
        self._repo = repo
        self._tz = tz
        self._projection = projection
        self._memory = memory

    def _vault_refresh(self, user_id: UUID) -> None:
        if self._projection:
            self._projection.tasks_changed(user_id)

    def create(
        self,
        user_id: UUID,
        title: str,
        *,
        kind: TaskKind = TaskKind.TODO,
        priority: TaskPriority = TaskPriority.MEDIUM,
        energy: TaskEnergy = TaskEnergy.MEDIUM,
        description: str | None = None,
        due: str | None = None,
        now: datetime,
    ) -> Task:
        # Seeds never carry deadlines — urgency is what makes a todo a todo.
        due_at = _parse_local_due(due, self._tz) if kind == TaskKind.TODO else None
        task = self._repo.save(Task(
            id=uuid4(),
            user_id=user_id,
            title=title,
            status=TaskStatus.OPEN,
            priority=priority,
            energy=energy,
            kind=kind,
            description=description,
            due_at=due_at,
            created_at=now,
            updated_at=now,
        ))
        # Seeds are the associative gold — half-formed curiosities that should
        # resurface when a new thought rhymes with them. Todos are transient, so
        # only seeds get filed into the meaning index.
        if task.kind == TaskKind.SEED and self._memory is not None:
            self._memory.remember(user_id, "seed", task.id, task.embedding_text())
        self._vault_refresh(user_id)
        return task

    def list_open(self, user_id: UUID) -> list[Task]:
        """Open todos — the real list. Seeds live in list_seeds."""
        return [t for t in self._repo.list_open(user_id) if t.kind == TaskKind.TODO]

    def list_seeds(self, user_id: UUID) -> list[Task]:
        return [t for t in self._repo.list_open(user_id) if t.kind == TaskKind.SEED]

    def list_parked(self, user_id: UUID) -> list[Task]:
        return self._repo.list_parked(user_id)

    def complete(self, user_id: UUID, task_id: UUID, *, now: datetime) -> Task:
        task = self._repo.get(task_id)
        if task is None or task.user_id != user_id:
            raise TaskNotFoundError(task_id)
        updated = self._repo.update(task_id, status=TaskStatus.DONE, completed_at=now)
        self._repo.save_event(TaskEvent(
            id=uuid4(), task_id=task_id, user_id=user_id,
            event_type="completed", reason=None, occurred_at=now,
        ))
        self._vault_refresh(user_id)
        return updated

    def update(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        title: str | None = None,
        priority: TaskPriority | None = None,
        energy: TaskEnergy | None = None,
        kind: TaskKind | None = None,
        status: TaskStatus | None = None,
        due: str | None = None,
        description: str | None = None,
        now: datetime,
    ) -> Task:
        task = self._repo.get(task_id)
        if task is None or task.user_id != user_id:
            raise TaskNotFoundError(task_id)
        kwargs: dict[str, Any] = {"updated_at": now}
        if title is not None:
            kwargs["title"] = title
        if priority is not None:
            kwargs["priority"] = priority
        if energy is not None:
            kwargs["energy"] = energy
        if kind is not None:
            kwargs["kind"] = kind
        if status is not None:
            kwargs["status"] = status
        if description is not None:
            kwargs["description"] = description
        if due is not None:
            kwargs["due_at"] = _parse_local_due(due, self._tz)
        updated = self._repo.update(task_id, **kwargs)
        # A dropped seed should stop surfacing in recall (e.g. one that just
        # graduated into an effort — the effort carries the meaning now).
        if updated.status == TaskStatus.DROPPED and self._memory is not None:
            self._memory.forget("seed", task_id)
        self._vault_refresh(user_id)
        return updated

    def drop(self, user_id: UUID, task_id: UUID, *, now: datetime) -> Task:
        task = self._repo.get(task_id)
        if task is None or task.user_id != user_id:
            raise TaskNotFoundError(task_id)
        dropped = self._repo.update(task_id, status=TaskStatus.DROPPED, updated_at=now)
        if self._memory is not None:
            self._memory.forget("seed", task_id)
        self._vault_refresh(user_id)
        return dropped

    def delete(self, user_id: UUID, task_id: UUID) -> bool:
        """Erase an erroneous task (duplicate, mis-extraction) — not a decision.
        A task she decided against gets status=dropped; a task that should
        never have existed is deleted so it cannot pollute history."""
        deleted = self._repo.delete(user_id, task_id)
        if deleted:
            if self._memory is not None:
                self._memory.forget("seed", task_id)
            self._vault_refresh(user_id)
        return deleted

    def overdue(self, user_id: UUID, now: datetime) -> list[Task]:
        return [t for t in self.list_open(user_id) if t.is_overdue(now)]

    def due_today(self, user_id: UUID, now: datetime) -> list[Task]:
        today = now.astimezone(self._tz).date()
        return [
            t for t in self.list_open(user_id)
            if t.due_at and t.due_at.astimezone(self._tz).date() == today
        ]


# ---------------------------------------------------------------------------
# ReminderService
# ---------------------------------------------------------------------------

class ReminderService:
    def __init__(
        self,
        repo: ReminderRepository,
        tz: tzinfo,
        projection: VaultProjection | None = None,
    ) -> None:
        self._repo = repo
        self._tz = tz
        self._projection = projection

    def _vault_refresh(self, user_id: UUID) -> None:
        if self._projection:
            self._projection.tasks_changed(user_id)

    def set(
        self,
        user_id: UUID,
        label: str,
        remind_at: datetime,
        *,
        task_id: UUID | None = None,
        recur_daily: bool = False,
        now: datetime,
    ) -> Reminder:
        reminder = self._repo.save(Reminder(
            id=uuid4(),
            user_id=user_id,
            label=label,
            remind_at=remind_at,
            status="scheduled",
            task_id=task_id,
            recur_daily=recur_daily,
            created_at=now,
        ))
        self._vault_refresh(user_id)
        return reminder

    def cancel(self, reminder_id: UUID) -> None:
        reminder = self._repo.get(reminder_id)
        self._repo.cancel(reminder_id)
        if reminder:
            self._vault_refresh(reminder.user_id)

    def upcoming(self, user_id: UUID, *, hours: int = 24, now: datetime) -> list[Reminder]:
        before = now + timedelta(hours=hours)
        return self._repo.list_upcoming(user_id, before=before)

    def mark_sent(self, reminder_id: UUID) -> None:
        reminder = self._repo.get(reminder_id)
        self._repo.mark_sent(reminder_id)
        if reminder:
            self._vault_refresh(reminder.user_id)

    def recent(self, user_id: UUID, *, limit: int = 10) -> list[Reminder]:
        return self._repo.list_recent(user_id, limit=limit)

    def reschedule_daily(self, user_id: UUID, reminder: Reminder, *, now: datetime) -> Reminder:
        return self.set(
            user_id,
            reminder.label,
            reminder.remind_at + timedelta(days=1),
            task_id=reminder.task_id,
            recur_daily=True,
            now=now,
        )


# ---------------------------------------------------------------------------
# GoalService
# ---------------------------------------------------------------------------

class GoalService:
    def __init__(self, repo: GoalRepository) -> None:
        self._repo = repo

    def add(
        self,
        user_id: UUID,
        title: str,
        goal_type: GoalType,
        *,
        target_date: date | None = None,
        is_fixed_date: bool = False,
        notes: str | None = None,
        now: datetime,
    ) -> Goal:
        return self._repo.save(Goal(
            id=uuid4(),
            user_id=user_id,
            title=title,
            goal_type=goal_type,
            target_date=target_date,
            is_fixed_date=is_fixed_date,
            notes=notes,
            created_at=now,
            updated_at=now,
        ))

    def list_active(self, user_id: UUID) -> list[Goal]:
        return self._repo.list_active(user_id)

    def list_training_goals(self, user_id: UUID) -> list[Goal]:
        return [g for g in self._repo.list_active(user_id) if g.is_training_goal()]

    def update(
        self,
        user_id: UUID,
        goal_id: UUID,
        *,
        title: str | None = None,
        target_date: date | None = None,
        is_fixed_date: bool | None = None,
        notes: str | None = None,
        status: GoalStatus | None = None,
        now: datetime,
    ) -> Goal:
        goal = self._repo.get(goal_id)
        if goal is None or goal.user_id != user_id:
            raise GoalNotFoundError(goal_id)
        kwargs: dict[str, Any] = {"updated_at": now}
        if title is not None:
            kwargs["title"] = title
        if target_date is not None:
            kwargs["target_date"] = target_date
        if is_fixed_date is not None:
            kwargs["is_fixed_date"] = is_fixed_date
        if notes is not None:
            kwargs["notes"] = notes
        if status is not None:
            kwargs["status"] = status
        return self._repo.update(goal_id, **kwargs)

    def achieve(self, user_id: UUID, goal_id: UUID, *, now: datetime) -> Goal:
        return self.update(user_id, goal_id, status=GoalStatus.ACHIEVED, now=now)

    def summary_for_context(self, user_id: UUID) -> str | None:
        goals = self._repo.list_active(user_id)
        if not goals:
            return None
        return "\n".join(f"- {g.summary()}" for g in goals)


# ---------------------------------------------------------------------------
# CleanupService — weekly review: surfaces unassigned captures, suggests efforts
# ---------------------------------------------------------------------------

class CleanupService:
    def __init__(
        self,
        capture_repo: CaptureRepository,
        effort_repo: EffortRepository,
        claude: BrainDumpClaude,
    ) -> None:
        self._captures = capture_repo
        self._efforts = effort_repo
        self._claude = claude

    def inbox(self, user_id: UUID, *, days: int = 30) -> list[Capture]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        return self._captures.list_unassigned(user_id, since=since)

    def suggest_efforts(self, user_id: UUID, *, days: int = 30) -> list:
        captures = self.inbox(user_id, days=days)
        summaries = [c.summary for c in captures if c.summary]
        if not summaries:
            return []
        return self._claude.suggest_efforts(summaries)

    def apply(
        self,
        user_id: UUID,
        assignments: list[CleanupAssignment],
    ) -> CleanupSummary:
        assigned = tasks_created = archived = 0
        for a in assignments:
            if a.action == "assigned" and a.effort_id:
                self._captures.assign_to_effort(a.capture_id, a.effort_id)
                assigned += 1
            elif a.action == "archived":
                self._captures.archive(a.capture_id)
                archived += 1

        return CleanupSummary(
            captures_reviewed=len(assignments),
            assigned=assigned,
            tasks_created=tasks_created,
            archived=archived,
            effort_suggestions=(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_local_due(value: str | None, tz: tzinfo) -> datetime | None:
    """Parse an explicit user-local due date from Claude: "YYYY-MM-DD[THH:MM]".

    Date-only values default to 09:00. Timezone attachment happens here —
    date/time resolution is Claude's job (it has the current date), timezone
    math is Python's. Unparseable input returns None rather than guessing.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.hour == 0 and parsed.minute == 0 and "T" not in value:
        parsed = parsed.replace(hour=9)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _effort_obsidian_path(title: str) -> str:
    safe = re.sub(r'[^\w\s-]', '', title).strip()
    safe = re.sub(r'\s+', ' ', safe)
    return f"Efforts/{safe}.md"
