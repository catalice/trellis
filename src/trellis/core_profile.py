from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID


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


class UserProfileRepository(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None: ...
    def upsert(self, profile: UserProfile) -> UserProfile: ...


class CurrentContextRepository(Protocol):
    def get(self, user_id: UUID) -> CurrentContext | None: ...
    def upsert(self, context: CurrentContext) -> CurrentContext: ...


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

    def clear(self, user_id: UUID, fields: list[str] | None = None) -> None:
        existing = self.repository.get(user_id)
        if existing is None:
            return
        clear_all = not fields
        ctx = CurrentContext(
            user_id=user_id,
            physical_notes=None if (clear_all or "physical_notes" in (fields or [])) else existing.physical_notes,
            cognitive_notes=None if (clear_all or "cognitive_notes" in (fields or [])) else existing.cognitive_notes,
            misc_notes=None if (clear_all or "misc_notes" in (fields or [])) else existing.misc_notes,
            valid_until=existing.valid_until,
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.upsert(ctx)
