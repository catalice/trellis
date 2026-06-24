from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ArcPhase:
    name: str
    focus: str
    start_date: date
    end_date: date
    weekly_runs: int
    long_run_minutes: int
    intensity: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "focus": self.focus,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "weekly_runs": self.weekly_runs,
            "long_run_minutes": self.long_run_minutes,
            "intensity": self.intensity,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArcPhase:
        return cls(
            name=d["name"],
            focus=d["focus"],
            start_date=date.fromisoformat(d["start_date"]),
            end_date=date.fromisoformat(d["end_date"]),
            weekly_runs=int(d["weekly_runs"]),
            long_run_minutes=int(d["long_run_minutes"]),
            intensity=d.get("intensity", "easy"),
            notes=d.get("notes", ""),
        )


@dataclass(frozen=True)
class TrainingArc:
    id: UUID
    user_id: UUID
    goal_id: UUID | None
    phases: list[ArcPhase]
    notes: str | None
    generated_at: datetime

    def current_phase(self, today: date) -> ArcPhase | None:
        for phase in self.phases:
            if phase.start_date <= today <= phase.end_date:
                return phase
        return None

    def phase_week(self, today: date) -> tuple[int, int] | None:
        """Return (current_week, total_weeks) within the current phase, or None."""
        phase = self.current_phase(today)
        if phase is None:
            return None
        elapsed = (today - phase.start_date).days
        total = (phase.end_date - phase.start_date).days
        week = elapsed // 7 + 1
        total_weeks = max(1, total // 7)
        return week, total_weeks

    def summary_for_coach(self, today: date) -> str:
        phase = self.current_phase(today)
        if phase is None:
            return "No active training phase."
        week_info = self.phase_week(today)
        week_str = f"week {week_info[0]} of {week_info[1]}" if week_info else ""
        lines = [
            f"Current phase: {phase.name} ({week_str})",
            f"Focus: {phase.focus}",
            f"Target: {phase.weekly_runs} runs/week, long run to {phase.long_run_minutes} mins",
            f"Intensity: {phase.intensity}",
        ]
        if phase.notes:
            lines.append(f"Notes: {phase.notes}")
        upcoming = [p for p in self.phases if p.start_date > today]
        if upcoming:
            next_phase = upcoming[0]
            lines.append(f"Next phase: {next_phase.name} from {next_phase.start_date.isoformat()}")
        return "\n".join(lines)


class ArcRepository(Protocol):
    def save(self, arc: TrainingArc) -> TrainingArc: ...
    def get_active(self, user_id: UUID) -> TrainingArc | None: ...
    def deactivate_all(self, user_id: UUID) -> None: ...
    def deactivate_others(self, user_id: UUID, keep_id: UUID) -> None: ...
