from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CaptureType(StrEnum):
    BRAIN_DUMP = "brain_dump"
    IDEA = "idea"
    TASK = "task"
    QUESTION = "question"
    REFERENCE = "reference"


class EffortIntensity(StrEnum):
    ACTIVE = "active"
    SIMMERING = "simmering"
    DORMANT = "dormant"
    FUTURE = "future"


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"      # decided never — invisible everywhere
    ARCHIVED = "archived"
    PARKED = "parked"        # consciously shelved — visible in its own section


class TaskKind(StrEnum):
    TODO = "todo"            # admin you owe — counted, can be overdue
    SEED = "seed"            # curiosity you might feed — never urgent, never counted


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskEnergy(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GoalType(StrEnum):
    RACE = "race"
    AEROBIC = "aerobic"
    STRENGTH = "strength"
    LIFE = "life"
    HABIT = "habit"
    GENERAL = "general"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    PAUSED = "paused"
    DROPPED = "dropped"


# ---------------------------------------------------------------------------
# Brain dump synthesis — returned by Claude, not stored directly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractedTask:
    title: str
    kind: TaskKind = TaskKind.TODO
    energy: TaskEnergy = TaskEnergy.MEDIUM
    priority: TaskPriority = TaskPriority.MEDIUM
    due: str | None = None  # explicit user-local "YYYY-MM-DD[THH:MM]"; Python attaches tz


@dataclass(frozen=True)
class BrainDumpResult:
    cleaned_text: str
    capture_type: CaptureType
    summary: str                          # one-line, used in Tier 2 snapshot and Obsidian daily page
    extracted_tasks: tuple[ExtractedTask, ...]
    questions: tuple[str, ...]
    effort_hints: tuple[str, ...]         # recurring topics that might warrant an Effort


# ---------------------------------------------------------------------------
# Capture — the atomic unit; brain dumps, ideas, questions, references
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capture:
    id: UUID
    user_id: UUID
    raw: str                              # always preserved exactly as sent
    capture_type: CaptureType
    synthesis: str | None                 # cleaned version; None if not yet synthesised
    summary: str | None                   # one-line for listings and daily page
    effort_id: UUID | None                # set during cleanup or auto-detected
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def compose_embedding_text(
        summary: str | None, synthesis: str | None, raw: str | None
    ) -> str:
        """The text a capture is embedded from — its meaning, richest first,
        de-duplicated. Single source of truth, shared by embed-on-write and the
        backfill script so the two can never drift apart."""
        seen: list[str] = []
        for part in (summary, synthesis, raw):
            text = (part or "").strip()
            if text and text not in seen:
                seen.append(text)
        return "\n".join(seen)

    def embedding_text(self) -> str:
        return self.compose_embedding_text(self.summary, self.synthesis, self.raw)


# ---------------------------------------------------------------------------
# Effort — forms from recurring themes; not declared, emerged
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Effort:
    id: UUID
    user_id: UUID
    title: str
    intensity: EffortIntensity
    notes: str | None
    obsidian_path: str | None             # e.g. "Efforts/Agricultural Revolution.md"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def describe(self) -> str:
        return f"{self.title} ({self.intensity.value})"

    @staticmethod
    def compose_embedding_text(title: str | None, notes: str | None) -> str:
        """The text an effort is embedded from. Single source of truth (see
        Capture.compose_embedding_text) — shared by embed-on-write and backfill."""
        seen: list[str] = []
        for part in (title, notes):
            text = (part or "").strip()
            if text and text not in seen:
                seen.append(text)
        return "\n".join(seen)

    def embedding_text(self) -> str:
        return self.compose_embedding_text(self.title, self.notes)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Task:
    id: UUID
    user_id: UUID
    title: str
    status: TaskStatus
    priority: TaskPriority
    energy: TaskEnergy
    kind: TaskKind = TaskKind.TODO
    description: str | None = None
    due_at: datetime | None = None
    source_capture_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def is_overdue(self, now: datetime) -> bool:
        return (
            self.due_at is not None
            and self.due_at < now
            and self.status == TaskStatus.OPEN
        )

    @staticmethod
    def compose_embedding_text(title: str | None, description: str | None) -> str:
        """The text a task/seed is embedded from. Single source of truth (see
        Capture.compose_embedding_text) — shared by embed-on-write and backfill.
        Only seeds are actually filed into the meaning index; todos are transient."""
        seen: list[str] = []
        for part in (title, description):
            text = (part or "").strip()
            if text and text not in seen:
                seen.append(text)
        return "\n".join(seen)

    def embedding_text(self) -> str:
        return self.compose_embedding_text(self.title, self.description)


@dataclass(frozen=True)
class TaskEvent:
    id: UUID
    task_id: UUID
    user_id: UUID
    event_type: str
    reason: str | None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Reminder — standalone or attached to a task; appointments live here
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reminder:
    id: UUID
    user_id: UUID
    label: str
    remind_at: datetime
    status: str                           # scheduled | sent | cancelled
    task_id: UUID | None = None
    recur_daily: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Goal — all types; training module reads .is_training_goal() subset
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Goal:
    id: UUID
    user_id: UUID
    title: str
    goal_type: GoalType
    status: GoalStatus = GoalStatus.ACTIVE
    target_date: date | None = None
    is_fixed_date: bool = False
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_training_goal(self) -> bool:
        return self.goal_type in (GoalType.RACE, GoalType.AEROBIC, GoalType.STRENGTH)

    def summary(self) -> str:
        parts = [f"{self.goal_type.value}: {self.title}"]
        if self.target_date:
            parts.append(
                f"target {self.target_date.isoformat()}"
                + (" (fixed)" if self.is_fixed_date else "")
            )
        if self.notes:
            parts.append(self.notes)
        return " — ".join(parts)


# ---------------------------------------------------------------------------
# (Self-tracking — StateLog / TrackingEvent / TrackingEventType — moved to the
# Sense room: domain_sense_models.py.)
