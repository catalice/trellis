# Wire in main.py:
# holistic_load = HolisticLoadService(health_repository)
# coach_service = CoachService(..., holistic_load=holistic_load)
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from trellis.health import GarminActivityRecord


class ActivitySource(Protocol):
    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]: ...


@dataclass(frozen=True)
class WeeklyLoadSignal:
    running_minutes_7d: int
    other_activity_minutes_7d: int
    total_hard_sessions_7d: int
    rationale: tuple[str, ...]


_STRENGTH_TYPES = frozenset({"strength_training", "fitness_equipment", "weight_training"})
_SPORT_TYPES = frozenset({"boxing", "martial_arts", "muay_thai", "cardio"})
_EASY_TYPES = frozenset({"cycling", "swimming", "hiking", "walking"})


def _activity_date(activity: GarminActivityRecord) -> date | None:
    if activity.start_time_epoch_seconds is None:
        return None
    return datetime.fromtimestamp(
        activity.start_time_epoch_seconds,
        timezone.utc,
    ).date()


def _duration_minutes(activity: GarminActivityRecord) -> int:
    return int((activity.duration_milliseconds or 0) / 60_000)


class HolisticLoadService:
    def __init__(self, repository: ActivitySource) -> None:
        self.repository = repository

    def weekly_signal(self, user_id: UUID, as_of: date) -> WeeklyLoadSignal:
        all_activities = self.repository.latest_activities(user_id, limit=50)

        window_start = as_of - timedelta(days=6)
        recent = tuple(
            a for a in all_activities
            if (d := _activity_date(a)) is not None and window_start <= d <= as_of
        )

        running_minutes = 0
        other_minutes = 0
        hard_sessions = 0
        rationale_parts: list[str] = []

        for activity in recent:
            atype = activity.activity_type.casefold()
            minutes = _duration_minutes(activity)

            if "running" in atype:
                running_minutes += minutes
                hard_sessions += 1
            elif atype in _STRENGTH_TYPES:
                other_minutes += minutes
                hard_sessions += 1
            elif atype in _SPORT_TYPES:
                other_minutes += minutes
                hard_sessions += 1
            elif atype in _EASY_TYPES:
                other_minutes += minutes
            elif minutes > 30:
                other_minutes += minutes

        if running_minutes:
            rationale_parts.append(f"{running_minutes}m running counted.")
        if other_minutes:
            rationale_parts.append(f"{other_minutes}m other activity counted.")
        rationale_parts.append(f"{hard_sessions} hard sessions in the last 7 days.")

        return WeeklyLoadSignal(
            running_minutes_7d=running_minutes,
            other_activity_minutes_7d=other_minutes,
            total_hard_sessions_7d=hard_sessions,
            rationale=tuple(rationale_parts),
        )
