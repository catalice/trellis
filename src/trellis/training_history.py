from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from trellis.health import GarminActivityRecord


class TrainingHistoryRepository(Protocol):
    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]: ...


@dataclass(frozen=True)
class TrainingHistorySummary:
    runs_28d: int
    distance_28d_km: float
    minutes_28d: int
    longest_run_84d_minutes: int | None
    longest_run_84d_km: float | None
    longest_run_anchor_minutes: int
    rationale: tuple[str, ...]


class TrainingHistoryService:
    def __init__(self, repository: TrainingHistoryRepository, *, activity_limit: int = 120):
        self.repository = repository
        self.activity_limit = activity_limit

    def summarize(
        self,
        user_id: UUID,
        *,
        as_of: date,
    ) -> TrainingHistorySummary:
        activities = tuple(
            activity
            for activity in self.repository.latest_activities(
                user_id,
                limit=self.activity_limit,
                activity_type="running",
            )
            if _activity_date(activity) is not None and _activity_date(activity) <= as_of
        )
        recent_28 = _within_days(activities, as_of=as_of, days=28)
        recent_84 = _within_days(activities, as_of=as_of, days=84)

        distance_28d_km = sum((activity.distance_meters or 0) for activity in recent_28) / 1000
        minutes_28d = round(
            sum((activity.duration_milliseconds or 0) for activity in recent_28) / 60_000
        )
        longest = max(
            recent_84,
            key=lambda activity: activity.duration_milliseconds or 0,
            default=None,
        )
        longest_minutes = (
            round((longest.duration_milliseconds or 0) / 60_000)
            if longest is not None
            else None
        )
        longest_km = (
            round((longest.distance_meters or 0) / 1000, 1)
            if longest is not None and longest.distance_meters is not None
            else None
        )
        anchor_minutes = _longest_run_anchor_minutes(
            runs_28d=len(recent_28),
            longest_minutes=longest_minutes,
        )
        rationale = _rationale(
            runs_28d=len(recent_28),
            distance_28d_km=distance_28d_km,
            longest_minutes=longest_minutes,
        )

        return TrainingHistorySummary(
            runs_28d=len(recent_28),
            distance_28d_km=round(distance_28d_km, 1),
            minutes_28d=minutes_28d,
            longest_run_84d_minutes=longest_minutes,
            longest_run_84d_km=longest_km,
            longest_run_anchor_minutes=anchor_minutes,
            rationale=rationale,
        )


def _within_days(
    activities: tuple[GarminActivityRecord, ...],
    *,
    as_of: date,
    days: int,
) -> tuple[GarminActivityRecord, ...]:
    start = as_of - timedelta(days=days - 1)
    return tuple(
        activity
        for activity in activities
        if (activity_date := _activity_date(activity)) is not None
        and start <= activity_date <= as_of
    )


def _activity_date(activity: GarminActivityRecord) -> date | None:
    if activity.start_time_epoch_seconds is None:
        return None
    return datetime.fromtimestamp(
        activity.start_time_epoch_seconds,
        timezone.utc,
    ).date()


def _longest_run_anchor_minutes(
    *,
    runs_28d: int,
    longest_minutes: int | None,
) -> int:
    """Returns a rounded anchor from recent history for Claude to reason against.

    Not a recommendation — Claude decides the actual long run target.
    """
    if longest_minutes is None:
        return 60
    if runs_28d <= 4:
        return max(40, min(60, _round_to_5(longest_minutes)))
    return max(50, min(90, _round_to_5(longest_minutes + 5)))


def _round_to_5(minutes: int) -> int:
    return int(round(minutes / 5) * 5)


def _rationale(
    *,
    runs_28d: int,
    distance_28d_km: float,
    longest_minutes: int | None,
) -> tuple[str, ...]:
    lines = [
        f"{runs_28d} runs and {distance_28d_km:.1f} km in the last 28 days."
    ]
    if longest_minutes is not None:
        lines.append(f"Longest run in last 84 days: {longest_minutes} minutes.")
    else:
        lines.append("No long-run history available in the last 84 days.")
    return tuple(lines)
