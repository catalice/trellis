from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.garmin.models import GarminActivity, GarminDailyHealth


class HealthProvider(StrEnum):
    GARMIN = "garmin"
    SELF_REPORT = "self_report"


class HealthSyncKind(StrEnum):
    DAILY_HEALTH = "daily_health"
    ACTIVITIES = "activities"
    ACTIVITY_DETAILS = "activity_details"


class HealthSyncStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class GarminHealthProvenance:
    provider: HealthProvider = HealthProvider.GARMIN
    sync_run_id: UUID | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    worker_endpoint: str | None = None


@dataclass(frozen=True)
class GarminDailyHealthRecord:
    user_id: UUID
    observed_on: date
    steps: int | None = None
    calories: int | None = None
    distance_meters: float | None = None
    active_minutes: int | None = None
    resting_heart_rate: int | None = None
    average_heart_rate: int | None = None
    maximum_heart_rate: int | None = None
    sleep_duration_minutes: int | None = None
    sleep_score: int | None = None
    body_battery_maximum: int | None = None
    body_battery_minimum: int | None = None
    body_battery_end: int | None = None
    average_stress: int | None = None
    hrv_weekly_average: float | None = None
    hrv_last_night: float | None = None
    hrv_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    provenance: GarminHealthProvenance = field(default_factory=GarminHealthProvenance)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_garmin(
        cls,
        user_id: UUID,
        health: GarminDailyHealth,
        *,
        provenance: GarminHealthProvenance | None = None,
    ) -> GarminDailyHealthRecord:
        return cls(
            user_id=user_id,
            observed_on=health.date,
            steps=health.steps,
            calories=health.calories,
            distance_meters=health.distance_meters,
            active_minutes=health.active_minutes,
            resting_heart_rate=health.resting_heart_rate,
            average_heart_rate=health.average_heart_rate,
            maximum_heart_rate=health.maximum_heart_rate,
            sleep_duration_minutes=health.sleep_duration_minutes,
            sleep_score=_bounded_int(health.sleep_score, low=0, high=100),
            body_battery_maximum=_bounded_int(health.body_battery_maximum, low=0, high=100),
            body_battery_minimum=_bounded_int(health.body_battery_minimum, low=0, high=100),
            body_battery_end=_bounded_int(health.body_battery_end, low=0, high=100),
            average_stress=_bounded_int(health.average_stress, low=0, high=100),
            hrv_weekly_average=health.hrv_weekly_average,
            hrv_last_night=health.hrv_last_night,
            hrv_status=health.hrv_status,
            raw=dict(health.raw),
            provenance=provenance or GarminHealthProvenance(),
        )


def _bounded_int(value: int | None, *, low: int, high: int) -> int | None:
    if value is None:
        return None
    if low <= value <= high:
        return value
    return None


@dataclass(frozen=True)
class GarminActivityRecord:
    user_id: UUID
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
    raw: dict[str, Any] = field(default_factory=dict)
    provenance: GarminHealthProvenance = field(default_factory=GarminHealthProvenance)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_garmin(
        cls,
        user_id: UUID,
        activity: GarminActivity,
        *,
        provenance: GarminHealthProvenance | None = None,
    ) -> GarminActivityRecord:
        return cls(
            user_id=user_id,
            activity_id=activity.activity_id,
            name=activity.name,
            activity_type=activity.activity_type,
            start_time_epoch_seconds=activity.start_time_epoch_seconds,
            duration_milliseconds=activity.duration_milliseconds,
            calories=activity.calories,
            average_heart_rate=activity.average_heart_rate,
            maximum_heart_rate=activity.maximum_heart_rate,
            distance_meters=activity.distance_meters,
            elevation_gain_meters=activity.elevation_gain_meters,
            elevation_loss_meters=activity.elevation_loss_meters,
            raw=dict(activity.raw),
            provenance=provenance or GarminHealthProvenance(),
        )


@dataclass(frozen=True)
class SelfHealthReport:
    user_id: UUID
    observed_on: date
    energy_score: int | None = None
    life_load_score: int | None = None
    sleep_minutes: int | None = None
    body_score: int | None = None
    soreness_score: int | None = None
    note: str | None = None
    source_capture_id: UUID | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    reported_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for label, score in (
            ("energy_score", self.energy_score),
            ("life_load_score", self.life_load_score),
            ("body_score", self.body_score),
            ("soreness_score", self.soreness_score),
        ):
            if score is not None and not 1 <= score <= 10:
                raise ValueError(f"{label} must be between 1 and 10")
        if self.sleep_minutes is not None and self.sleep_minutes < 0:
            raise ValueError("sleep_minutes cannot be negative")


@dataclass(frozen=True)
class HealthSyncRun:
    user_id: UUID
    kind: HealthSyncKind
    started_at: datetime
    start_date: date | None = None
    end_date: date | None = None
    status: HealthSyncStatus = HealthSyncStatus.RUNNING
    provider: HealthProvider = HealthProvider.GARMIN
    id: UUID = field(default_factory=uuid4)
    completed_at: datetime | None = None
    records_upserted: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def succeeded(
        self,
        *,
        completed_at: datetime,
        records_upserted: int,
        metadata: dict[str, Any] | None = None,
    ) -> HealthSyncRun:
        if records_upserted < 0:
            raise ValueError("records_upserted cannot be negative")
        return replace(
            self,
            status=HealthSyncStatus.SUCCEEDED,
            completed_at=completed_at,
            records_upserted=records_upserted,
            error=None,
            metadata=metadata if metadata is not None else self.metadata,
        )

    def failed(
        self,
        *,
        completed_at: datetime,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> HealthSyncRun:
        if not error.strip():
            raise ValueError("error cannot be empty")
        return replace(
            self,
            status=HealthSyncStatus.FAILED,
            completed_at=completed_at,
            error=error[:2000],
            metadata=metadata if metadata is not None else self.metadata,
        )


class HealthRepository(Protocol):
    def upsert_daily_health(
        self,
        record: GarminDailyHealthRecord,
    ) -> GarminDailyHealthRecord:
        ...

    def get_daily_health(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> GarminDailyHealthRecord | None:
        ...

    def resting_heart_rate_baseline(
        self,
        user_id: UUID,
        *,
        before: date,
        days: int = 60,
    ) -> int | None:
        ...

    def upsert_activity(self, record: GarminActivityRecord) -> GarminActivityRecord:
        ...

    def get_activity(
        self,
        user_id: UUID,
        activity_id: str,
    ) -> GarminActivityRecord | None:
        ...

    def record_self_report(self, report: SelfHealthReport) -> SelfHealthReport:
        ...

    def list_self_reports(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        ...

    def start_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        ...

    def finish_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        ...

    def get_sync(self, sync_run_id: UUID) -> HealthSyncRun | None:
        ...


class InMemoryHealthRepository:
    def __init__(self) -> None:
        self._daily_health: dict[tuple[UUID, date], GarminDailyHealthRecord] = {}
        self._activities: dict[tuple[UUID, str], GarminActivityRecord] = {}
        self._self_reports: dict[UUID, SelfHealthReport] = {}
        self._sync_runs: dict[UUID, HealthSyncRun] = {}

    def upsert_daily_health(
        self,
        record: GarminDailyHealthRecord,
    ) -> GarminDailyHealthRecord:
        stored = replace(record, raw=dict(record.raw))
        self._daily_health[(record.user_id, record.observed_on)] = stored
        return stored

    def get_daily_health(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> GarminDailyHealthRecord | None:
        return self._daily_health.get((user_id, observed_on))

    def resting_heart_rate_baseline(
        self,
        user_id: UUID,
        *,
        before: date,
        days: int = 60,
    ) -> int | None:
        from datetime import timedelta

        start = before - timedelta(days=days)
        values = [
            record.resting_heart_rate
            for (record_user_id, observed_on), record in self._daily_health.items()
            if record_user_id == user_id
            and start <= observed_on < before
            and record.resting_heart_rate is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values))

    def upsert_activity(self, record: GarminActivityRecord) -> GarminActivityRecord:
        stored = replace(record, raw=dict(record.raw))
        self._activities[(record.user_id, record.activity_id)] = stored
        return stored

    def get_activity(
        self,
        user_id: UUID,
        activity_id: str,
    ) -> GarminActivityRecord | None:
        return self._activities.get((user_id, activity_id))

    def record_self_report(self, report: SelfHealthReport) -> SelfHealthReport:
        stored = replace(report, raw=dict(report.raw))
        self._self_reports[stored.id] = stored
        return stored

    def list_self_reports(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        reports = [
            report
            for report in self._self_reports.values()
            if report.user_id == user_id and report.observed_on == observed_on
        ]
        return tuple(sorted(reports, key=lambda report: report.reported_at))

    def start_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        if run.status is not HealthSyncStatus.RUNNING:
            raise ValueError("sync run must start in running status")
        self._sync_runs[run.id] = run
        return run

    def finish_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        if run.id not in self._sync_runs:
            raise LookupError(run.id)
        if run.status is HealthSyncStatus.RUNNING:
            raise ValueError("finished sync run cannot still be running")
        self._sync_runs[run.id] = run
        return run

    def get_sync(self, sync_run_id: UUID) -> HealthSyncRun | None:
        return self._sync_runs.get(sync_run_id)
