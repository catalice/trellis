from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class TrainingAnchor:
    id: UUID
    user_id: UUID
    day_of_week: int  # 0=Mon, 6=Sun
    time_of_day: str | None  # "09:00"
    kind: str  # matches SessionKind values: 'strength', 'social_run', etc.
    label: str  # human-readable: "PT with trainer"
    is_hard_constraint: bool = True

    @property
    def day_name(self) -> str:
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][self.day_of_week]

    def describe(self) -> str:
        parts = [self.day_name]
        if self.time_of_day:
            parts.append(self.time_of_day)
        parts.append(self.label)
        if not self.is_hard_constraint:
            parts.append("(soft)")
        return " ".join(parts)


@dataclass(frozen=True)
class UserProfile:
    user_id: UUID
    name: str | None
    physical_notes: str | None
    cognitive_notes: str | None
    updated_at: datetime

    def is_empty(self) -> bool:
        return not self.name and not self.physical_notes and not self.cognitive_notes

    def for_coach(self) -> str:
        lines = []
        if self.name:
            lines.append(f"Name: {self.name}")
        if self.physical_notes:
            lines.append(f"Physical: {self.physical_notes}")
        if self.cognitive_notes:
            lines.append(f"Cognitive/exec: {self.cognitive_notes}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CurrentContext:
    user_id: UUID
    physical_notes: str | None
    cognitive_notes: str | None
    misc_notes: str | None
    valid_until: date
    updated_at: datetime

    def is_valid(self, today: date) -> bool:
        return self.valid_until >= today

    def for_coach(self) -> str:
        lines = []
        if self.physical_notes:
            lines.append(f"Physical (current): {self.physical_notes}")
        if self.cognitive_notes:
            lines.append(f"Life/cognitive (current): {self.cognitive_notes}")
        if self.misc_notes:
            lines.append(f"Other (current): {self.misc_notes}")
        return "\n".join(lines)


class TrainingAnchorRepository(Protocol):
    def list_active(self, user_id: UUID) -> list[TrainingAnchor]: ...
    def save(self, anchor: TrainingAnchor) -> TrainingAnchor: ...
    def deactivate(self, anchor_id: UUID) -> None: ...


class UserProfileRepository(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None: ...
    def upsert(self, profile: UserProfile) -> UserProfile: ...


class CurrentContextRepository(Protocol):
    def get(self, user_id: UUID) -> CurrentContext | None: ...
    def upsert(self, context: CurrentContext) -> CurrentContext: ...


class AnchorService:
    def __init__(self, repository: TrainingAnchorRepository) -> None:
        self.repository = repository

    def list(self, user_id: UUID) -> list[TrainingAnchor]:
        return self.repository.list_active(user_id)

    def set(
        self,
        user_id: UUID,
        day_of_week: int,
        kind: str,
        label: str,
        *,
        time_of_day: str | None = None,
        is_hard_constraint: bool = True,
    ) -> TrainingAnchor:
        existing = self.repository.list_active(user_id)
        for a in existing:
            if a.day_of_week == day_of_week and a.kind == kind:
                self.repository.deactivate(a.id)
        anchor = TrainingAnchor(
            id=uuid4(),
            user_id=user_id,
            day_of_week=day_of_week,
            time_of_day=time_of_day,
            kind=kind,
            label=label,
            is_hard_constraint=is_hard_constraint,
        )
        return self.repository.save(anchor)

    def remove(self, anchor_id: UUID) -> None:
        self.repository.deactivate(anchor_id)

    def summary_for_coach(self, user_id: UUID) -> str | None:
        anchors = self.list(user_id)
        if not anchors:
            return None
        return "\n".join(a.describe() for a in anchors)


class UserProfileService:
    def __init__(self, repository: UserProfileRepository) -> None:
        self.repository = repository

    def get(self, user_id: UUID) -> UserProfile | None:
        return self.repository.get(user_id)

    def update(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        physical_notes: str | None = None,
        cognitive_notes: str | None = None,
    ) -> UserProfile:
        existing = self.repository.get(user_id)
        profile = UserProfile(
            user_id=user_id,
            name=name if name is not None else (existing.name if existing else None),
            physical_notes=physical_notes if physical_notes is not None
                           else (existing.physical_notes if existing else None),
            cognitive_notes=cognitive_notes if cognitive_notes is not None
                            else (existing.cognitive_notes if existing else None),
            updated_at=datetime.now(timezone.utc),
        )
        return self.repository.upsert(profile)


class CurrentContextService:
    def __init__(self, repository: CurrentContextRepository) -> None:
        self.repository = repository

    def get_valid(self, user_id: UUID, today: date) -> CurrentContext | None:
        ctx = self.repository.get(user_id)
        if ctx is None or not ctx.is_valid(today):
            return None
        return ctx

    def clear(self, user_id: UUID, fields: list[str] | None = None) -> None:
        """Set specified fields to None. Clears all three if fields is None or empty."""
        existing = self.repository.get(user_id)
        if existing is None:
            return
        clear_all = not fields
        ctx = CurrentContext(
            user_id=user_id,
            physical_notes=None if (clear_all or "physical_notes" in fields) else existing.physical_notes,
            cognitive_notes=None if (clear_all or "cognitive_notes" in fields) else existing.cognitive_notes,
            misc_notes=None if (clear_all or "misc_notes" in fields) else existing.misc_notes,
            valid_until=existing.valid_until,
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.upsert(ctx)

    def update(
        self,
        user_id: UUID,
        *,
        physical_notes: str | None = None,
        cognitive_notes: str | None = None,
        misc_notes: str | None = None,
        valid_days: int = 14,
        today: date,
    ) -> CurrentContext:
        existing = self.repository.get(user_id)
        ctx = CurrentContext(
            user_id=user_id,
            physical_notes=physical_notes if physical_notes is not None
                           else (existing.physical_notes if existing else None),
            cognitive_notes=cognitive_notes if cognitive_notes is not None
                            else (existing.cognitive_notes if existing else None),
            misc_notes=misc_notes if misc_notes is not None
                       else (existing.misc_notes if existing else None),
            valid_until=today + timedelta(days=valid_days),
            updated_at=datetime.now(timezone.utc),
        )
        return self.repository.upsert(ctx)
