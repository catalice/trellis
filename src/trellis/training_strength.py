from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Exercise:
    name: str
    sets: int | None = None
    reps: int | None = None
    weight_kg: float | None = None
    duration_seconds: int | None = None
    notes: str | None = None

    def display(self) -> str:
        parts = [self.name]
        if self.sets is not None and self.reps is not None:
            parts.append(f"{self.sets}×{self.reps}")
        elif self.sets is not None and self.duration_seconds is not None:
            secs = self.duration_seconds
            parts.append(f"{self.sets}×{secs}s")
        elif self.sets:
            parts.append(f"{self.sets} sets")
        if self.weight_kg is not None:
            parts.append(f"@{self.weight_kg:g}kg")
        if self.notes:
            parts.append(f"({self.notes})")
        return " ".join(parts)


@dataclass(frozen=True)
class StrengthSession:
    id: UUID
    user_id: UUID
    session_date: date
    exercises: tuple[Exercise, ...]
    program_phase: str | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class StrengthSessionRepository(Protocol):
    def save(self, session: StrengthSession) -> StrengthSession: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[StrengthSession]: ...


class StrengthSessionService:
    def __init__(self, repository: StrengthSessionRepository) -> None:
        self.repository = repository

    def record(
        self,
        user_id: UUID,
        session_date: date,
        exercises: list[dict],
        *,
        program_phase: str | None = None,
        notes: str | None = None,
    ) -> StrengthSession:
        parsed = tuple(_parse_exercise(e) for e in exercises)
        session = StrengthSession(
            id=uuid4(),
            user_id=user_id,
            session_date=session_date,
            exercises=parsed,
            program_phase=program_phase.strip().lower() if program_phase else None,
            notes=notes,
        )
        return self.repository.save(session)

    def list_recent(self, user_id: UUID, *, limit: int = 6) -> list[StrengthSession]:
        return self.repository.list_recent(user_id, limit=limit)


def _parse_exercise(data: dict) -> Exercise:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Exercise name cannot be empty")
    return Exercise(
        name=name,
        sets=_int_or_none(data.get("sets")),
        reps=_int_or_none(data.get("reps")),
        weight_kg=_float_or_none(data.get("weight_kg")),
        duration_seconds=_int_or_none(data.get("duration_seconds")),
        notes=data.get("notes") or None,
    )


def _int_or_none(val) -> int | None:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _float_or_none(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None
