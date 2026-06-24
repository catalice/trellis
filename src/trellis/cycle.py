from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class CycleEvent:
    id: UUID
    user_id: UUID
    event_type: str  # 'period_start' | 'observation'
    occurred_on: date
    note: str | None = None
    symptoms: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CycleRepository(Protocol):
    def record(self, event: CycleEvent) -> CycleEvent: ...
    def list_recent(self, user_id: UUID, *, limit: int = 10) -> list[CycleEvent]: ...
    def last_period_start(self, user_id: UUID) -> CycleEvent | None: ...


class CycleService:
    def __init__(self, repository: CycleRepository) -> None:
        self.repository = repository

    def record_period_start(
        self,
        user_id: UUID,
        occurred_on: date,
        *,
        note: str | None = None,
    ) -> CycleEvent:
        if occurred_on > date.today():
            raise ValueError(f"Period start cannot be in the future: {occurred_on}")
        event = CycleEvent(
            id=uuid4(),
            user_id=user_id,
            event_type="period_start",
            occurred_on=occurred_on,
            note=note,
        )
        return self.repository.record(event)

    def record_observation(
        self,
        user_id: UUID,
        occurred_on: date,
        *,
        note: str | None = None,
        symptoms: tuple[str, ...] = (),
    ) -> CycleEvent:
        event = CycleEvent(
            id=uuid4(),
            user_id=user_id,
            event_type="observation",
            occurred_on=occurred_on,
            note=note,
            symptoms=symptoms,
        )
        return self.repository.record(event)

    def current_phase(self, user_id: UUID, today: date) -> str | None:
        last_period = self.repository.last_period_start(user_id)
        if last_period is None:
            return None
        cycle_day = (today - last_period.occurred_on).days + 1
        if cycle_day <= 5:
            return "menstruation"
        if cycle_day <= 13:
            return "follicular"
        if cycle_day <= 16:
            return "ovulation"
        if cycle_day <= 24:
            return "luteal"
        return "late_luteal"

    def get_status(self, user_id: UUID, today: date) -> str:
        last_period = self.repository.last_period_start(user_id)
        if last_period is None:
            return "Cycle: no period start recorded yet."

        cycle_day = (today - last_period.occurred_on).days + 1

        if cycle_day <= 5:
            phase = f"menstruation (day {cycle_day})"
        elif cycle_day <= 13:
            phase = f"follicular (day {cycle_day})"
        elif cycle_day <= 16:
            phase = f"ovulation window (day {cycle_day})"
        elif cycle_day <= 28:
            phase = f"luteal (day {cycle_day})"
        else:
            phase = f"late luteal / period due (day {cycle_day})"

        return (
            f"Cycle: {phase}. "
            f"Period started {last_period.occurred_on.isoformat()}."
        )
