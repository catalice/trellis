"""Normalized Garmin records independent of the worker's wire format."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class GarminAuthStatus(str, Enum):
    SUCCESS = "success"
    MFA_REQUIRED = "mfa_required"


@dataclass(frozen=True, slots=True)
class GarminAuthResult:
    status: GarminAuthStatus
    session_dump: str | None = None
    mfa_session_id: str | None = None

    @property
    def requires_mfa(self) -> bool:
        return self.status is GarminAuthStatus.MFA_REQUIRED


@dataclass(frozen=True, slots=True)
class GarminDailyHealth:
    date: date
    steps: int | None = None
    calories: int | None = None
    distance_meters: float | None = None
    active_minutes: int | None = None
    floors_climbed: float | None = None
    resting_heart_rate: int | None = None
    average_heart_rate: int | None = None
    maximum_heart_rate: int | None = None
    sleep_duration_minutes: int | None = None
    sleep_deep_minutes: int | None = None
    sleep_light_minutes: int | None = None
    sleep_rem_minutes: int | None = None
    sleep_awake_minutes: int | None = None
    sleep_score: int | None = None
    body_battery_maximum: int | None = None
    body_battery_minimum: int | None = None
    body_battery_end: int | None = None
    body_battery_charged: int | None = None
    body_battery_drained: int | None = None
    average_stress: int | None = None
    maximum_stress: int | None = None
    stress_rest_minutes: int | None = None
    hrv_weekly_average: float | None = None
    hrv_last_night: float | None = None
    hrv_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GarminActivity:
    activity_id: str
    name: str
    activity_type: str
    start_time_epoch_seconds: int | None = None
    duration_milliseconds: float | None = None
    calories: int | None = None
    average_heart_rate: int | None = None
    maximum_heart_rate: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    elevation_loss_meters: float | None = None
    active_sets: int | None = None
    total_exercise_repetitions: int | None = None
    summarized_exercise_sets: Any = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GarminActivityDetail:
    activity_id: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def splits(self) -> Any:
        return self.raw.get("splits")

    @property
    def split_summaries(self) -> Any:
        return self.raw.get("splitSummaries")

    @property
    def typed_splits(self) -> Any:
        return self.raw.get("typedSplits")
