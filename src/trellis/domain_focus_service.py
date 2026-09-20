from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.core_actions import Erased
from trellis.domain_focus_models import (
    Capture,
    CaptureType,
    Effort,
    EffortIntensity,
    Goal,
    GoalStatus,
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
    def list_recent(self, user_id: UUID, *, limit: int) -> list[Capture]: ...
    def list_unassigned(self, user_id: UUID, *, since: date) -> list[Capture]: ...
    def assign_to_effort(self, user_id: UUID, capture_id: UUID, effort_id: UUID | None) -> Capture: ...
    def list_for_effort(self, user_id: UUID, effort_id: UUID) -> list[Capture]: ...
    def delete(self, user_id: UUID, capture_id: UUID) -> bool: ...
    def get(self, user_id: UUID, capture_id: UUID) -> Capture | None: ...
    def count_unassigned_before(self, user_id: UUID, *, before: date) -> int: ...


class EffortRepository(Protocol):
    def save(self, effort: Effort) -> Effort: ...
    def get(self, effort_id: UUID) -> Effort | None: ...
    def get_by_title(self, user_id: UUID, title: str) -> Effort | None: ...
    def list_all(self, user_id: UUID) -> list[Effort]: ...
    def delete(self, user_id: UUID, effort_id: UUID) -> bool: ...
    def rename(self, user_id: UUID, effort_id: UUID, title: str, obsidian_path: str) -> bool: ...


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
    def list_scheduled(self, user_id: UUID) -> list[Reminder]: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[Reminder]: ...
    def cancel(self, reminder_id: UUID) -> bool: ...
    def claim(self, reminder_id: UUID, *, now: datetime) -> bool: ...
    def ready(self, reminder_id: UUID, message: str) -> None: ...
    def awaiting_delivery(self, user_id: UUID) -> list[Reminder]: ...
    def delivery_failed(self, reminder_id: UUID) -> int: ...
    def accepted(self, reminder_id: UUID, *, now: datetime) -> None: ...
    def undelivered(self, reminder_id: UUID) -> None: ...


class GoalRepository(Protocol):
    def save(self, goal: Goal) -> Goal: ...
    def get(self, goal_id: UUID) -> Goal | None: ...
    def list_active(self, user_id: UUID) -> list[Goal]: ...
    def update(self, goal_id: UUID, **kwargs: Any) -> Goal: ...


class BrainDumpClaude(Protocol):
    def synthesise(
        self, raw_text: str, current_date_line: str, hints: str | None = None,
    ) -> BrainDumpResult | None: ...


class PageTaken(ValueError):
    """The vault page a rename would land on already exists."""
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


class VaultProjection(Protocol):
    """Write-only view of the second brain (Obsidian). Implementations must
    never raise — a failed vault write must not break the bot."""
    def capture_saved(self, capture: Capture, tasks: tuple[Task, ...] = ()) -> None: ...
    def tasks_changed(self, user_id: UUID) -> None: ...
    def effort_created(self, effort: Effort) -> None: ...
    def capture_assigned(self, capture: Capture) -> None: ...
    def research_saved(self, capture: Capture) -> None: ...
    def page_exists(self, obsidian_path: str) -> bool: ...
    def capture_erased(self, capture: Capture) -> list[str]: ...    # vault pages where its text could NOT be removed
    def effort_page_removed(self, obsidian_path: str) -> str: ...   # removed | kept | missing
    def effort_page_moved(self, old_path: str | None, effort: Effort, keep_old: bool = False) -> str: ...


class Memory(Protocol):
    """Semantic memory index (see infra_memory). Optional: when absent, writes
    still succeed unindexed and recall is unavailable. remember/forget never raise
    — indexing a row must never break the row's own write."""
    def remember(self, user_id: UUID, entity_kind: str, entity_id: UUID, text: str) -> None: ...
    def forget(self, entity_kind: str, entity_id: UUID) -> bool | None: ...   # False = the delete failed


# ---------------------------------------------------------------------------
# Result types (service-layer only — not stored)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessedDump:
    capture: Capture
    tasks_created: tuple[Task, ...]
    synthesis: BrainDumpResult | None     # None if Claude call failed; capture still saved
    duplicates_skipped: tuple[str, ...] = ()  # extracted titles already on the open list


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
        hints_provider=None,   # (user_id) -> str | None: efforts/threads for routing
    ) -> None:
        self._captures = capture_repo
        self._tasks = task_repo
        self._claude = claude
        self._tz = timezone
        self._projection = projection
        self._memory = memory
        self._hints = hints_provider

    def process(self, user_id: UUID, raw: str, now: datetime) -> ProcessedDump:
        local = now.astimezone(self._tz)
        date_line = local.strftime("%A %d %B %Y, %H:%M") + f" ({self._tz})"
        hints = None
        if self._hints is not None:
            try:
                hints = self._hints(user_id)
            except Exception:
                _log.warning("brain dump hints failed", exc_info=True)
        result = self._claude.synthesise(raw, date_line, hints=hints)

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
        duplicates: list[str] = []
        if result:
            # Dedup against the open list: the same dump sent twice must not
            # create the same tasks twice. Exact title match only — anything
            # fuzzier is a judgement, and judgements go to Claude.
            try:
                existing_titles = {
                    t.title.strip().lower() for t in self._tasks.list_open(user_id)
                }
            except Exception:
                _log.warning("brain dump dedup check failed", exc_info=True)
                existing_titles = set()
            for extracted in result.extracted_tasks:
                if extracted.title.strip().lower() in existing_titles:
                    duplicates.append(extracted.title)
                    continue
                # Seeds never carry deadlines — same rule as TaskService.create,
                # enforced on BOTH creation paths so they can't drift.
                due_at = (
                    _parse_local_due(extracted.due, self._tz)
                    if extracted.kind == TaskKind.TODO else None
                )
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

        return ProcessedDump(
            capture=capture, tasks_created=tuple(tasks), synthesis=result,
            duplicates_skipped=tuple(duplicates),
        )


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

    def count_unassigned_older_than(self, user_id: UUID, *, days: int = 30) -> int:
        before = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        return self._repo.count_unassigned_before(user_id, before=before)

    def get(self, user_id: UUID, capture_id: UUID) -> Capture | None:
        return self._repo.get(user_id, capture_id)

    def for_effort(self, user_id: UUID, effort_id: UUID) -> list[Capture]:
        return self._repo.list_for_effort(user_id, effort_id)

    def assign(self, user_id: UUID, capture_id: UUID, effort_id: UUID) -> Capture:
        capture = self._repo.assign_to_effort(user_id, capture_id, effort_id)
        if self._projection:
            self._projection.capture_assigned(capture)
        return capture

    def erase(self, user_id: UUID, capture_id: UUID) -> "Erased":
        """Erase a capture everywhere Trellis put it: the record, its search
        entry, and the text Trellis wrote into the vault. Writing done by hand
        is never touched; a block someone edited is left and named. Tasks taken
        from it are erased separately, by their own ids."""
        capture = self._repo.get(user_id, capture_id)
        if capture is None or not self._repo.delete(user_id, capture_id):
            return Erased(erased=False)
        uncertain: list[str] = []
        if self._memory is not None and self._memory.forget("capture", capture_id) is False:
            uncertain.append("the search index")
        left: list[str] = []
        if self._projection is not None:
            try:
                left = list(self._projection.capture_erased(capture))
            except Exception:
                _log.warning("capture erase: vault text not removed", exc_info=True)
                uncertain.append("the vault")
        # capture_erased reports a clean-up it could not finish as "the vault (…)".
        uncertain += [page for page in left if page.startswith("the vault")]
        left = [page for page in left if not page.startswith("the vault")]
        return Erased(erased=True, left_in_vault=tuple(left), uncertain=tuple(uncertain))

    def delete(self, user_id: UUID, capture_id: UUID) -> bool:
        return self.erase(user_id, capture_id).erased

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

    def find_or_create(self, user_id: UUID, title: str, now: datetime) -> Effort:
        """Return the effort with this title, creating it (active) if new.
        This is how a seed graduates: research it, and it gets a home."""
        existing = self._repo.get_by_title(user_id, title)
        if existing is not None:
            return existing
        effort_id = uuid4()
        path = _effort_obsidian_path(title)
        if self._page_taken(user_id, path):
            # Another effort's title sanitises to this name, or a page was
            # written there by hand. A new effort never moves into a page
            # that isn't its own.
            path = path[:-len(".md")] + f" ({str(effort_id)[:8]}).md"
        effort = self._repo.save(Effort(
            id=effort_id,
            user_id=user_id,
            title=title,
            intensity=EffortIntensity.ACTIVE,
            notes=None,
            obsidian_path=path,
            created_at=now,
            updated_at=now,
        ))
        self._embed(effort)
        if self._projection:
            self._projection.effort_created(effort)
        return effort

    def list_all(self, user_id: UUID) -> list[Effort]:
        return self._repo.list_all(user_id)

    def page(self, user_id: UUID, title: str, captures) -> "tuple[Effort, list[Capture]] | None":
        """One effort's full page: the effort + every capture filed on it."""
        effort = self._repo.get_by_title(user_id, title)
        if effort is None:
            return None
        return effort, captures.for_effort(user_id, effort.id)

    def _page_shared(self, user_id: UUID, path: str, effort_id: UUID) -> bool:
        """Older installs can hold two efforts on one page (titles that sanitise
        alike). Until they're separated, that page is never moved or removed."""
        return any(e.obsidian_path == path and e.id != effort_id for e in self._repo.list_all(user_id))

    def _page_taken(self, user_id: UUID, path: str, *, ignoring: UUID | None = None) -> bool:
        if any(e.obsidian_path == path and e.id != ignoring for e in self._repo.list_all(user_id)):
            return True
        return bool(self._projection is not None and self._projection.page_exists(path))

    def delete_if_empty(self, user_id: UUID, effort_id: UUID, captures) -> str:
        """Erase an effort ONLY when nothing is filed on it — the empty guard
        is fact (Python), never judgment. 'deleted' | 'deleted_page_kept' |
        'not_empty' | 'not_found'. The record goes; a page someone has written
        on by hand stays, and the caller says so."""
        effort = self._repo.get(effort_id)
        if effort is None or effort.user_id != user_id:
            return "not_found"
        if captures.for_effort(user_id, effort_id):
            return "not_empty"
        old_path = effort.obsidian_path
        shared = bool(old_path) and self._page_shared(user_id, old_path, effort_id)
        if not self._repo.delete(user_id, effort_id):
            return "not_found"
        if self._memory is not None:
            self._memory.forget("effort", effort_id)
        if shared:
            return "deleted_page_kept"      # another effort still lives on that page
        if self._projection is not None and old_path:
            try:
                if self._projection.effort_page_removed(old_path) == "kept":
                    return "deleted_page_kept"
            except Exception:
                _log.warning("effort page removal failed", exc_info=True)
                return "deleted_page_kept"
        return "deleted"

    def rename(self, user_id: UUID, effort_id: UUID, new_title: str) -> Effort | None:
        """Rename: title + vault page moved with it — never an orphaned ghost."""
        effort = self._repo.get(effort_id)
        if effort is None or effort.user_id != user_id or not new_title.strip():
            return None
        old_path = effort.obsidian_path
        new_path = _effort_obsidian_path(new_title)
        if new_path != old_path and self._page_taken(user_id, new_path, ignoring=effort_id):
            # Checked BEFORE anything changes: a rename never lands on a page
            # that belongs to another effort or was written by hand.
            raise PageTaken(new_path)
        shared = bool(old_path) and self._page_shared(user_id, old_path, effort_id)
        if not self._repo.rename(user_id, effort_id, new_title, new_path):
            return None
        renamed = self._repo.get(effort_id)
        if renamed is None:
            return None
        self._embed(renamed)
        if self._projection is not None:
            try:
                self._projection.effort_page_moved(old_path, renamed, keep_old=shared)
            except Exception:
                _log.warning("effort page move failed", exc_info=True)
        return renamed

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
        """Parked todos — parked seeds stay out of the tasks view."""
        return [t for t in self._repo.list_parked(user_id) if t.kind == TaskKind.TODO]

    def complete(self, user_id: UUID, task_id: UUID, *, now: datetime) -> Task:
        task = self._repo.get(task_id)
        if task is None or task.user_id != user_id:
            raise TaskNotFoundError(task_id)
        # Idempotent: re-completing keeps the original completed_at and doesn't
        # append a second event — the event history stays honest.
        if task.status == TaskStatus.DONE:
            return task
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

    def delete(self, user_id: UUID, task_id: UUID) -> bool:
        """Erase an erroneous task (duplicate, mis-extraction) — not a decision.
        A task they decided against gets status=dropped; a task that should
        never have existed is deleted so it cannot pollute history."""
        return self.erase(user_id, task_id).erased

    def erase(self, user_id: UUID, task_id: UUID) -> Erased:
        """The same erase, store by store: a search entry that could not be
        removed is named, not assumed gone."""
        if not self._repo.delete(user_id, task_id):
            return Erased(erased=False)
        uncertain: list[str] = []
        if self._memory is not None and self._memory.forget("seed", task_id) is False:
            uncertain.append("the search index")
        self._vault_refresh(user_id)
        return Erased(erased=True, uncertain=tuple(uncertain))

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
        recurrence: str | None = None,
        kind: str = "remind",
        now: datetime,
    ) -> Reminder:
        reminder = self._repo.save(Reminder(
            id=uuid4(),
            user_id=user_id,
            label=label,
            remind_at=remind_at,
            status="scheduled",
            task_id=task_id,
            recurrence=recurrence,
            kind=kind,
            created_at=now,
        ))
        self._vault_refresh(user_id)
        return reminder

    def cancel(self, reminder_id: UUID) -> bool:
        """True only if a scheduled reminder was actually cancelled."""
        reminder = self._repo.get(reminder_id)
        cancelled = self._repo.cancel(reminder_id)
        if cancelled and reminder:
            self._vault_refresh(reminder.user_id)
        return cancelled

    def upcoming(self, user_id: UUID, *, hours: int = 24, now: datetime) -> list[Reminder]:
        before = now + timedelta(hours=hours)
        return self._repo.list_upcoming(user_id, before=before)

    def all_scheduled(self, user_id: UUID) -> list[Reminder]:
        """EVERY scheduled reminder, however far out — the management view.
        (The time-windowed upcoming() is for snapshots and delivery; a reminder
        the model can't SEE is one it can't cancel or update — audit item 25.)"""
        return self._repo.list_scheduled(user_id)

    # -- delivery: claimed -> executed (message ready) -> accepted | undelivered --

    def claim(self, reminder_id: UUID, *, now: datetime) -> bool:
        return self._repo.claim(reminder_id, now=now)

    def ready(self, reminder_id: UUID, message: str) -> None:
        self._repo.ready(reminder_id, message)

    def awaiting_delivery(self, user_id: UUID) -> list[Reminder]:
        return self._repo.awaiting_delivery(user_id)

    def delivery_failed(self, reminder_id: UUID) -> int:
        return self._repo.delivery_failed(reminder_id)

    def accepted(self, reminder_id: UUID, *, now: datetime) -> None:
        reminder = self._repo.get(reminder_id)
        self._repo.accepted(reminder_id, now=now)
        if reminder:
            self._vault_refresh(reminder.user_id)

    def undelivered(self, reminder_id: UUID) -> None:
        self._repo.undelivered(reminder_id)

    def recent(self, user_id: UUID, *, limit: int = 10) -> list[Reminder]:
        return self._repo.list_recent(user_id, limit=limit)

    def reschedule(self, user_id: UUID, reminder: Reminder, *, now: datetime) -> Reminder:
        """Schedule the next occurrence of a recurring reminder — always strictly
        in the future, so downtime can't queue a backlog of stale firings."""
        return self.set(
            user_id,
            reminder.label,
            _next_occurrence(reminder.remind_at, reminder.recurrence or "daily", now, self._tz),
            task_id=reminder.task_id,
            recurrence=reminder.recurrence,
            kind=reminder.kind,
            now=now,
        )


# ---------------------------------------------------------------------------
# GoalService
# ---------------------------------------------------------------------------

_UNSET = object()  # sentinel: "field not sent" vs "explicitly cleared to None"


class GoalService:
    def __init__(self, repo: GoalRepository) -> None:
        self._repo = repo

    def add(
        self,
        user_id: UUID,
        title: str,
        *,
        label: str | None = None,
        target_date: date | None = None,
        is_fixed_date: bool = False,
        notes: str | None = None,
        now: datetime,
    ) -> Goal:
        return self._repo.save(Goal(
            id=uuid4(),
            user_id=user_id,
            title=title,
            label=(label or "").strip() or None,
            target_date=target_date,
            is_fixed_date=is_fixed_date,
            notes=notes,
            created_at=now,
            updated_at=now,
        ))

    def list_active(self, user_id: UUID) -> list[Goal]:
        return self._repo.list_active(user_id)

    def get(self, user_id: UUID, goal_id: UUID) -> Goal | None:
        goal = self._repo.get(goal_id)
        if goal is None or goal.user_id != user_id:
            return None
        return goal

    def list_training_goals(self, user_id: UUID) -> list[Goal]:
        return [g for g in self._repo.list_active(user_id) if g.is_training_goal()]

    def update(
        self,
        user_id: UUID,
        goal_id: UUID,
        *,
        title: str | None = None,
        label: str | None = _UNSET,  # None is meaningful: it clears the label
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
        if label is not _UNSET:
            kwargs["label"] = label
        if target_date is not None:
            kwargs["target_date"] = target_date
        if is_fixed_date is not None:
            kwargs["is_fixed_date"] = is_fixed_date
        if notes is not None:
            kwargs["notes"] = notes
        if status is not None:
            kwargs["status"] = status
        return self._repo.update(goal_id, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_occurrence(after: datetime, recurrence: str, now: datetime, tz: tzinfo | None = None) -> datetime:
    """The next firing, at the same time ON THE WALL in the person's timezone.
    A recurrence is a local habit ("09:00 every day"), not a fixed gap between
    instants: stepping the stored UTC instant by 24h drifts it an hour at every
    clock change. Arithmetic on a datetime in a named zone is wall-clock
    arithmetic, so the step is taken there and converted back."""
    local = after.astimezone(tz) if tz is not None else after
    nxt = _advance(local, recurrence)
    while nxt <= now:
        nxt = _advance(nxt, recurrence)
    return nxt.astimezone(timezone.utc) if tz is not None else nxt


def _advance(dt: datetime, recurrence: str) -> datetime:
    if recurrence == "weekly":
        return dt + timedelta(days=7)
    if recurrence == "monthly":
        return _add_months(dt, 1)
    if recurrence == "yearly":
        return _add_months(dt, 12)
    return dt + timedelta(days=1)  # daily


def _add_months(dt: datetime, months: int) -> datetime:
    """Same wall-clock time, N months on — day clamped to the target month's
    length (31 Jan + 1 month = 28/29 Feb), so "monthly" never skips a month."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


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
        # Visible, not silent: an unparseable due means the task lands undated
        # while the user believes the deadline stuck.
        _log.warning("unparseable due date %r — task will have no due date", value)
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
