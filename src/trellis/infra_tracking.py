"""Health records, readiness scoring, and cycle tracking.

Consolidates: health.py, health_postgres.py, readiness.py,
              readiness_service.py, cycle.py
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from psycopg2.extras import RealDictCursor

if TYPE_CHECKING:
    from trellis.infra_garmin import GarminActivity, GarminDailyHealth


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

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


class ReadinessBand(StrEnum):
    LOW = "low"
    STEADY = "steady"
    READY = "ready"
    STRONG = "strong"


# ---------------------------------------------------------------------------
# Health record models
# ---------------------------------------------------------------------------

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
        self, *, completed_at: datetime, records_upserted: int,
        metadata: dict[str, Any] | None = None,
    ) -> HealthSyncRun:
        if records_upserted < 0:
            raise ValueError("records_upserted cannot be negative")
        return replace(
            self, status=HealthSyncStatus.SUCCEEDED, completed_at=completed_at,
            records_upserted=records_upserted, error=None,
            metadata=metadata if metadata is not None else self.metadata,
        )

    def failed(
        self, *, completed_at: datetime, error: str,
        metadata: dict[str, Any] | None = None,
    ) -> HealthSyncRun:
        if not error.strip():
            raise ValueError("error cannot be empty")
        return replace(
            self, status=HealthSyncStatus.FAILED, completed_at=completed_at,
            error=error[:2000],
            metadata=metadata if metadata is not None else self.metadata,
        )


# ---------------------------------------------------------------------------
# Health repository protocol
# ---------------------------------------------------------------------------

class HealthRepository(Protocol):
    def upsert_daily_health(self, record: GarminDailyHealthRecord) -> GarminDailyHealthRecord: ...
    def get_daily_health(self, user_id: UUID, observed_on: date) -> GarminDailyHealthRecord | None: ...
    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None: ...
    def resting_heart_rate_baseline(self, user_id: UUID, *, before: date, days: int = 60) -> int | None: ...
    def upsert_activity(self, record: GarminActivityRecord) -> GarminActivityRecord: ...
    def get_activity(self, user_id: UUID, activity_id: str) -> GarminActivityRecord | None: ...
    def latest_activity(self, user_id: UUID) -> GarminActivityRecord | None: ...
    def latest_activities(self, user_id: UUID, *, limit: int, activity_type: str | None = None) -> tuple[GarminActivityRecord, ...]: ...
    def upsert_activity_detail(self, *, user_id: UUID, activity_id: str, raw_data: dict[str, Any], sync_run_id: UUID | None) -> None: ...
    def record_self_report(self, report: SelfHealthReport) -> SelfHealthReport: ...
    def list_self_reports(self, user_id: UUID, observed_on: date) -> tuple[SelfHealthReport, ...]: ...
    def start_sync(self, run: HealthSyncRun) -> HealthSyncRun: ...
    def finish_sync(self, run: HealthSyncRun) -> HealthSyncRun: ...
    def get_sync(self, sync_run_id: UUID) -> HealthSyncRun | None: ...


# ---------------------------------------------------------------------------
# Postgres health repository
# ---------------------------------------------------------------------------

class PostgresHealthRepository:
    def __init__(self, database) -> None:
        self.database = database

    def upsert_daily_health(self, record: GarminDailyHealthRecord) -> GarminDailyHealthRecord:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO garmin_daily_health (
                        user_id, observed_on, steps, calories, distance_meters,
                        active_minutes, resting_heart_rate, average_heart_rate,
                        maximum_heart_rate, sleep_duration_minutes, sleep_score,
                        body_battery_maximum, body_battery_minimum, body_battery_end,
                        average_stress, hrv_weekly_average, hrv_last_night,
                        hrv_status, raw_data, provenance, sync_run_id, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (user_id, observed_on) DO UPDATE SET
                        steps = EXCLUDED.steps,
                        calories = EXCLUDED.calories,
                        distance_meters = EXCLUDED.distance_meters,
                        active_minutes = EXCLUDED.active_minutes,
                        resting_heart_rate = EXCLUDED.resting_heart_rate,
                        average_heart_rate = EXCLUDED.average_heart_rate,
                        maximum_heart_rate = EXCLUDED.maximum_heart_rate,
                        sleep_duration_minutes = EXCLUDED.sleep_duration_minutes,
                        sleep_score = EXCLUDED.sleep_score,
                        body_battery_maximum = EXCLUDED.body_battery_maximum,
                        body_battery_minimum = EXCLUDED.body_battery_minimum,
                        body_battery_end = EXCLUDED.body_battery_end,
                        average_stress = EXCLUDED.average_stress,
                        hrv_weekly_average = EXCLUDED.hrv_weekly_average,
                        hrv_last_night = EXCLUDED.hrv_last_night,
                        hrv_status = EXCLUDED.hrv_status,
                        raw_data = EXCLUDED.raw_data,
                        provenance = EXCLUDED.provenance,
                        sync_run_id = EXCLUDED.sync_run_id,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        record.user_id, record.observed_on, record.steps, record.calories,
                        record.distance_meters, record.active_minutes, record.resting_heart_rate,
                        record.average_heart_rate, record.maximum_heart_rate,
                        record.sleep_duration_minutes, record.sleep_score,
                        record.body_battery_maximum, record.body_battery_minimum,
                        record.body_battery_end, record.average_stress,
                        record.hrv_weekly_average, record.hrv_last_night, record.hrv_status,
                        _json(record.raw), _json(_provenance(record.provenance)),
                        record.provenance.sync_run_id, record.updated_at,
                    ),
                )
                return self._daily_health(cursor.fetchone())

    def get_daily_health(self, user_id: UUID, observed_on: date) -> GarminDailyHealthRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_daily_health WHERE user_id = %s AND observed_on = %s",
                    (user_id, observed_on),
                )
                row = cursor.fetchone()
                return self._daily_health(row) if row else None

    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_daily_health WHERE user_id = %s"
                    " ORDER BY observed_on DESC LIMIT 1",
                    (user_id,),
                )
                row = cursor.fetchone()
                return self._daily_health(row) if row else None

    def resting_heart_rate_baseline(
        self, user_id: UUID, *, before: date, days: int = 60,
    ) -> int | None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ROUND(AVG(resting_heart_rate))::INTEGER
                    FROM garmin_daily_health
                    WHERE user_id = %s
                      AND observed_on >= %s::date - (%s || ' days')::interval
                      AND observed_on < %s
                      AND resting_heart_rate IS NOT NULL
                    """,
                    (user_id, before, days, before),
                )
                return cursor.fetchone()[0]

    def upsert_activity(self, record: GarminActivityRecord) -> GarminActivityRecord:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO garmin_activities (
                        user_id, activity_id, name, activity_type,
                        start_time_epoch_seconds, duration_milliseconds, calories,
                        average_heart_rate, maximum_heart_rate, distance_meters,
                        elevation_gain_meters, elevation_loss_meters,
                        raw_data, provenance, sync_run_id, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (user_id, activity_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        activity_type = EXCLUDED.activity_type,
                        start_time_epoch_seconds = EXCLUDED.start_time_epoch_seconds,
                        duration_milliseconds = EXCLUDED.duration_milliseconds,
                        calories = EXCLUDED.calories,
                        average_heart_rate = EXCLUDED.average_heart_rate,
                        maximum_heart_rate = EXCLUDED.maximum_heart_rate,
                        distance_meters = EXCLUDED.distance_meters,
                        elevation_gain_meters = EXCLUDED.elevation_gain_meters,
                        elevation_loss_meters = EXCLUDED.elevation_loss_meters,
                        raw_data = EXCLUDED.raw_data,
                        provenance = EXCLUDED.provenance,
                        sync_run_id = EXCLUDED.sync_run_id,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        record.user_id, record.activity_id, record.name, record.activity_type,
                        record.start_time_epoch_seconds, record.duration_milliseconds,
                        record.calories, record.average_heart_rate, record.maximum_heart_rate,
                        record.distance_meters, record.elevation_gain_meters,
                        record.elevation_loss_meters,
                        _json(record.raw), _json(_provenance(record.provenance)),
                        record.provenance.sync_run_id, record.updated_at,
                    ),
                )
                return self._activity(cursor.fetchone())

    def get_activity(self, user_id: UUID, activity_id: str) -> GarminActivityRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_activities WHERE user_id = %s AND activity_id = %s",
                    (user_id, activity_id),
                )
                row = cursor.fetchone()
                return self._activity(row) if row else None

    def latest_activity(self, user_id: UUID) -> GarminActivityRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_activities WHERE user_id = %s"
                    " ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC LIMIT 1",
                    (user_id,),
                )
                row = cursor.fetchone()
                return self._activity(row) if row else None

    def latest_activities(
        self, user_id: UUID, *, limit: int, activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                if activity_type:
                    cursor.execute(
                        "SELECT * FROM garmin_activities WHERE user_id = %s AND activity_type = %s"
                        " ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC LIMIT %s",
                        (user_id, activity_type, limit),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM garmin_activities WHERE user_id = %s"
                        " ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC LIMIT %s",
                        (user_id, limit),
                    )
                return tuple(self._activity(row) for row in cursor.fetchall())

    def latest_activities_with_detail(self, user_id: UUID, *, limit: int) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        a.activity_id, a.name, a.activity_type,
                        a.start_time_epoch_seconds, a.duration_milliseconds,
                        a.calories, a.distance_meters, a.elevation_gain_meters,
                        COALESCE(
                            a.average_heart_rate,
                            (d.raw_data->'activity'->'summaryDTO'->>'averageHR')::numeric::int
                        ) AS average_heart_rate,
                        COALESCE(
                            a.maximum_heart_rate,
                            (d.raw_data->'activity'->'summaryDTO'->>'maxHR')::numeric::int
                        ) AS maximum_heart_rate,
                        d.typed_splits
                    FROM garmin_activities a
                    LEFT JOIN garmin_activity_details d
                      ON a.user_id = d.user_id AND a.activity_id = d.activity_id
                    WHERE a.user_id = %s
                    ORDER BY a.start_time_epoch_seconds DESC NULLS LAST
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [dict(row) for row in cursor.fetchall()]

    def upsert_activity_detail(
        self, *, user_id: UUID, activity_id: str,
        raw_data: dict[str, Any], sync_run_id: UUID | None,
    ) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO garmin_activity_details (
                        user_id, activity_id, raw_data, splits, split_summaries,
                        typed_splits, exercise_sets, sync_run_id, updated_at
                    ) VALUES (
                        %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s, NOW()
                    )
                    ON CONFLICT (user_id, activity_id) DO UPDATE SET
                        raw_data = EXCLUDED.raw_data,
                        splits = EXCLUDED.splits,
                        split_summaries = EXCLUDED.split_summaries,
                        typed_splits = EXCLUDED.typed_splits,
                        exercise_sets = EXCLUDED.exercise_sets,
                        sync_run_id = EXCLUDED.sync_run_id,
                        updated_at = NOW()
                    """,
                    (
                        user_id, activity_id,
                        _json(raw_data),
                        _json(_detail_section(raw_data, "splits", [])),
                        _json(_detail_section(raw_data, "splitSummaries", {})),
                        _json(_detail_section(raw_data, "typedSplits", {})),
                        _json(_detail_section(raw_data, "exerciseSets", {})),
                        sync_run_id,
                    ),
                )
                summary_dto = (raw_data.get("activity") or {}).get("summaryDTO") or {}
                avg_hr = summary_dto.get("averageHR")
                max_hr = summary_dto.get("maxHR")
                if avg_hr is not None or max_hr is not None:
                    cursor.execute(
                        """
                        UPDATE garmin_activities SET
                            average_heart_rate = COALESCE(average_heart_rate, %s),
                            maximum_heart_rate = COALESCE(maximum_heart_rate, %s),
                            updated_at = NOW()
                        WHERE user_id = %s AND activity_id = %s
                          AND (average_heart_rate IS NULL OR maximum_heart_rate IS NULL)
                        """,
                        (
                            int(avg_hr) if avg_hr is not None else None,
                            int(max_hr) if max_hr is not None else None,
                            user_id, activity_id,
                        ),
                    )

    def latest_activity_detail(
        self, user_id: UUID, *, activity_type: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                if activity_type:
                    cursor.execute(
                        """
                        SELECT details.* FROM garmin_activity_details details
                        JOIN garmin_activities activities
                          ON activities.user_id = details.user_id
                         AND activities.activity_id = details.activity_id
                        WHERE details.user_id = %s AND activities.activity_type = %s
                        ORDER BY activities.start_time_epoch_seconds DESC NULLS LAST,
                                 details.updated_at DESC LIMIT 1
                        """,
                        (user_id, activity_type),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT details.* FROM garmin_activity_details details
                        JOIN garmin_activities activities
                          ON activities.user_id = details.user_id
                         AND activities.activity_id = details.activity_id
                        WHERE details.user_id = %s
                        ORDER BY activities.start_time_epoch_seconds DESC NULLS LAST,
                                 details.updated_at DESC LIMIT 1
                        """,
                        (user_id,),
                    )
                row = cursor.fetchone()
                if row is None:
                    return None
                return {
                    "activity_id": row["activity_id"],
                    "raw_data": dict(row["raw_data"] or {}),
                    "splits": row["splits"],
                    "split_summaries": row["split_summaries"],
                    "typed_splits": row["typed_splits"],
                    "exercise_sets": row["exercise_sets"],
                }

    def activity_detail(self, user_id: UUID, activity_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_activity_details WHERE user_id = %s AND activity_id = %s",
                    (user_id, activity_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return {
                    "activity_id": row["activity_id"],
                    "raw_data": dict(row["raw_data"] or {}),
                    "splits": row["splits"],
                    "split_summaries": row["split_summaries"],
                    "typed_splits": row["typed_splits"],
                    "exercise_sets": row["exercise_sets"],
                }

    def record_self_report(self, report: SelfHealthReport) -> SelfHealthReport:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO health_self_reports (
                        id, user_id, observed_on, energy_score, life_load_score,
                        sleep_minutes, body_score, soreness_score, note,
                        source_capture_id, raw_data, reported_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    RETURNING *
                    """,
                    (
                        report.id, report.user_id, report.observed_on,
                        report.energy_score, report.life_load_score, report.sleep_minutes,
                        report.body_score, report.soreness_score, report.note,
                        report.source_capture_id, _json(report.raw), report.reported_at,
                    ),
                )
                return self._self_report(cursor.fetchone())

    def list_self_reports(
        self, user_id: UUID, observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM health_self_reports WHERE user_id = %s AND observed_on = %s"
                    " ORDER BY reported_at",
                    (user_id, observed_on),
                )
                return tuple(self._self_report(row) for row in cursor.fetchall())

    def start_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        if run.status is not HealthSyncStatus.RUNNING:
            raise ValueError("sync run must start in running status")
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO health_sync_runs (
                        id, user_id, provider, sync_kind, status, start_date,
                        end_date, started_at, records_upserted, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        run.id, run.user_id, run.provider.value, run.kind.value,
                        run.status.value, run.start_date, run.end_date, run.started_at,
                        run.records_upserted, _json(run.metadata),
                    ),
                )
        return run

    def finish_sync(self, run: HealthSyncRun) -> HealthSyncRun:
        if run.status is HealthSyncStatus.RUNNING:
            raise ValueError("finished sync run cannot still be running")
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE health_sync_runs
                    SET status = %s, completed_at = %s, records_upserted = %s,
                        error = %s, metadata = %s::jsonb
                    WHERE id = %s
                    """,
                    (
                        run.status.value, run.completed_at, run.records_upserted,
                        run.error, _json(run.metadata), run.id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise LookupError(run.id)
        return run

    def get_sync(self, sync_run_id: UUID) -> HealthSyncRun | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM health_sync_runs WHERE id = %s", (sync_run_id,))
                row = cursor.fetchone()
                return self._sync_run(row) if row else None

    @staticmethod
    def _daily_health(row: dict[str, Any]) -> GarminDailyHealthRecord:
        return GarminDailyHealthRecord(
            user_id=row["user_id"],
            observed_on=row["observed_on"],
            steps=row["steps"],
            calories=row["calories"],
            distance_meters=float(row["distance_meters"]) if row["distance_meters"] is not None else None,
            active_minutes=row["active_minutes"],
            resting_heart_rate=row["resting_heart_rate"],
            average_heart_rate=row["average_heart_rate"],
            maximum_heart_rate=row["maximum_heart_rate"],
            sleep_duration_minutes=row["sleep_duration_minutes"],
            sleep_score=row["sleep_score"],
            body_battery_maximum=row["body_battery_maximum"],
            body_battery_minimum=row["body_battery_minimum"],
            body_battery_end=row["body_battery_end"],
            average_stress=row["average_stress"],
            hrv_weekly_average=float(row["hrv_weekly_average"]) if row["hrv_weekly_average"] is not None else None,
            hrv_last_night=float(row["hrv_last_night"]) if row["hrv_last_night"] is not None else None,
            hrv_status=row["hrv_status"],
            raw=dict(row["raw_data"] or {}),
            provenance=_load_provenance(row["provenance"] or {}),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _activity(row: dict[str, Any]) -> GarminActivityRecord:
        return GarminActivityRecord(
            user_id=row["user_id"],
            activity_id=row["activity_id"],
            name=row["name"],
            activity_type=row["activity_type"],
            start_time_epoch_seconds=row["start_time_epoch_seconds"],
            duration_milliseconds=float(row["duration_milliseconds"]) if row["duration_milliseconds"] is not None else None,
            calories=row["calories"],
            average_heart_rate=row["average_heart_rate"],
            maximum_heart_rate=row["maximum_heart_rate"],
            distance_meters=float(row["distance_meters"]) if row["distance_meters"] is not None else None,
            elevation_gain_meters=float(row["elevation_gain_meters"]) if row["elevation_gain_meters"] is not None else None,
            elevation_loss_meters=float(row["elevation_loss_meters"]) if row["elevation_loss_meters"] is not None else None,
            raw=dict(row["raw_data"] or {}),
            provenance=_load_provenance(row["provenance"] or {}),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _self_report(row: dict[str, Any]) -> SelfHealthReport:
        return SelfHealthReport(
            id=row["id"],
            user_id=row["user_id"],
            observed_on=row["observed_on"],
            energy_score=row["energy_score"],
            life_load_score=row["life_load_score"],
            sleep_minutes=row["sleep_minutes"],
            body_score=row["body_score"],
            soreness_score=row.get("soreness_score"),
            note=row["note"],
            source_capture_id=row["source_capture_id"],
            raw=dict(row["raw_data"] or {}),
            reported_at=row["reported_at"],
        )

    @staticmethod
    def _sync_run(row: dict[str, Any]) -> HealthSyncRun:
        return HealthSyncRun(
            id=row["id"],
            user_id=row["user_id"],
            provider=HealthProvider(row["provider"]),
            kind=HealthSyncKind(row["sync_kind"]),
            status=HealthSyncStatus(row["status"]),
            start_date=row["start_date"],
            end_date=row["end_date"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            records_upserted=row["records_upserted"],
            error=row["error"],
            metadata=dict(row["metadata"] or {}),
        )


# ---------------------------------------------------------------------------
# Readiness models and calculator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelfReport:
    energy: int
    body: int
    life_load: int
    soreness: int

    def __post_init__(self) -> None:
        for name, value in (
            ("energy", self.energy), ("body", self.body),
            ("life_load", self.life_load), ("soreness", self.soreness),
        ):
            if not 1 <= value <= 10:
                raise ValueError(f"{name} must be between 1 and 10")


@dataclass(frozen=True)
class DailyReadinessInput:
    date: date
    sleep_duration_minutes: int | None = None
    sleep_score: int | None = None
    body_battery: int | None = None
    resting_heart_rate: int | None = None
    resting_heart_rate_baseline: int | None = None
    hrv_last_night: float | None = None
    hrv_baseline: float | None = None
    average_stress: int | None = None
    self_report: SelfReport | None = None

    @classmethod
    def from_normalized_health(
        cls,
        health: Any,
        *,
        on_date: date,
        resting_heart_rate_baseline: int | None = None,
        hrv_baseline: float | None = None,
        self_report: SelfReport | None = None,
    ) -> DailyReadinessInput:
        return cls(
            date=on_date,
            sleep_duration_minutes=getattr(health, "sleep_duration_minutes", None),
            sleep_score=getattr(health, "sleep_score", None),
            body_battery=getattr(health, "body_battery_end", None)
            or getattr(health, "body_battery_maximum", None),
            resting_heart_rate=getattr(health, "resting_heart_rate", None),
            resting_heart_rate_baseline=resting_heart_rate_baseline,
            hrv_last_night=getattr(health, "hrv_last_night", None),
            hrv_baseline=hrv_baseline,
            average_stress=getattr(health, "average_stress", None),
            self_report=self_report,
        )


@dataclass(frozen=True)
class ReadinessContribution:
    name: str
    points: int
    rationale: str


@dataclass(frozen=True)
class ReadinessResult:
    date: date
    score: int
    band: ReadinessBand
    confidence: str
    contributions: tuple[ReadinessContribution, ...]
    rationale: tuple[str, ...]
    missing_metrics: tuple[str, ...]

    def with_score_for_test(self, score: int) -> ReadinessResult:
        return replace(self, score=score, band=_band(score))


class ReadinessCalculator:
    def assess(
        self,
        inputs: DailyReadinessInput,
        *,
        history: tuple[ReadinessResult, ...] = (),
    ) -> ReadinessResult:
        contributions: list[ReadinessContribution] = []
        rationale: list[str] = []
        missing: list[str] = []

        self._sleep(inputs, contributions, rationale, missing)
        self._body_battery(inputs, contributions, rationale, missing)
        self._heart_rate(inputs, contributions, missing)
        self._hrv(inputs, contributions, missing)
        self._stress(inputs, contributions, missing)
        self._self_report(inputs, contributions, rationale, missing)
        self._trend(history, contributions)

        score = 65 + sum(item.points for item in contributions)
        if inputs.self_report is None and self._objective_metric_count(inputs) >= 5:
            contributions.append(ReadinessContribution(
                "objective_data", 9, "Garmin signals are broad enough for a high-confidence estimate",
            ))
            score += 9
        if any(item.points <= -10 for item in contributions) and score >= 90:
            score = 89
        score = max(0, min(100, score))
        confidence = self._confidence(missing)
        if confidence == "low":
            rationale.append("Readiness is usable but low-confidence")
        if any(item.points < -8 for item in contributions) and score >= 75:
            rationale.append("No single poor metric is allowed to decide the day")

        return ReadinessResult(
            date=inputs.date, score=score, band=_band(score), confidence=confidence,
            contributions=tuple(contributions), rationale=tuple(rationale),
            missing_metrics=tuple(missing),
        )

    @staticmethod
    def _sleep(inputs, contributions, rationale, missing):
        if inputs.sleep_score is None and inputs.sleep_duration_minutes is None:
            missing.append("sleep"); return
        points = 0
        if inputs.sleep_score is not None:
            if inputs.sleep_score >= 85: points += 9; rationale.append("Sleep is strong")
            elif inputs.sleep_score >= 75: points += 5
            elif inputs.sleep_score < 60: points -= 8
        if inputs.sleep_duration_minutes is not None:
            if inputs.sleep_duration_minutes >= 450: points += 4
            elif inputs.sleep_duration_minutes < 360: points -= 6
        contributions.append(ReadinessContribution("sleep", points, "Sleep recovery signal"))

    @staticmethod
    def _body_battery(inputs, contributions, rationale, missing):
        if inputs.body_battery is None:
            missing.append("body_battery"); return
        if inputs.body_battery >= 80: points = 8
        elif inputs.body_battery >= 60: points = 6
        elif inputs.body_battery < 40: points = -10
        else: points = -4
        contributions.append(ReadinessContribution("body_battery", points, "Garmin body battery"))

    @staticmethod
    def _heart_rate(inputs, contributions, missing):
        if inputs.resting_heart_rate is None or inputs.resting_heart_rate_baseline is None:
            missing.append("resting_heart_rate"); return
        delta = inputs.resting_heart_rate - inputs.resting_heart_rate_baseline
        points = 4 if delta <= -2 else 2 if delta <= 1 else 0 if delta <= 3 else -4 if delta <= 5 else -8
        contributions.append(ReadinessContribution("resting_heart_rate", points, "Resting HR versus baseline"))

    @staticmethod
    def _hrv(inputs, contributions, missing):
        if inputs.hrv_last_night is None:
            missing.append("hrv_last_night"); return
        if inputs.hrv_last_night >= 55: points = 7
        elif inputs.hrv_last_night >= 45: points = 3
        elif inputs.hrv_last_night >= 35: points = 0
        else: points = -10
        contributions.append(ReadinessContribution("hrv", points, "Last-night HRV"))

    @staticmethod
    def _stress(inputs, contributions, missing):
        if inputs.average_stress is None:
            missing.append("stress"); return
        points = 4 if inputs.average_stress < 30 else 3 if inputs.average_stress < 45 else -5
        contributions.append(ReadinessContribution("stress", points, "Average stress"))

    @staticmethod
    def _self_report(inputs, contributions, rationale, missing):
        report = inputs.self_report
        if report is None:
            missing.append("self_report"); return
        points = 0
        points += round((report.energy - 5) * 1.5)
        points += round((report.body - 5) * 1.5)
        points -= round((report.life_load - 5) * 1.1)
        points -= round((report.soreness - 3) * 1.2)
        contributions.append(ReadinessContribution("self_report", points, "Subjective state"))

    @staticmethod
    def _trend(history, contributions):
        recent = tuple(result.score for result in sorted(history, key=lambda item: item.date)[-3:])
        if len(recent) < 3:
            return
        average = sum(recent) / len(recent)
        if average < 55:
            contributions.append(ReadinessContribution("trend", -6, f"Last 3 days average {average:.1f}"))
        elif average > 80:
            contributions.append(ReadinessContribution("trend", 3, f"Last 3 days average {average:.1f}"))

    @staticmethod
    def _confidence(missing):
        available = 6 - len([n for n in missing if n in {
            "sleep", "body_battery", "resting_heart_rate", "hrv_last_night", "stress", "self_report",
        }])
        return "high" if available >= 5 else "medium" if available >= 3 else "low"

    @staticmethod
    def _objective_metric_count(inputs):
        return sum(v is not None for v in (
            inputs.sleep_score, inputs.body_battery, inputs.resting_heart_rate,
            inputs.hrv_last_night, inputs.average_stress,
        ))


# ---------------------------------------------------------------------------
# Readiness service
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadinessSnapshot:
    user_id: UUID
    requested_on: date
    source_health_date: date | None
    used_latest_health_fallback: bool
    score: int
    band: ReadinessBand
    confidence: str
    contributions: tuple[ReadinessContribution, ...]
    rationale: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    data_lines: tuple[str, ...]
    self_report_id: UUID | None = None

    @property
    def has_garmin_data(self) -> bool:
        return self.source_health_date is not None


class ReadinessService:
    def __init__(
        self,
        repository: HealthRepository,
        *,
        calculator: ReadinessCalculator | None = None,
    ) -> None:
        self.repository = repository
        self.calculator = calculator or ReadinessCalculator()

    def today(
        self,
        user_id: UUID,
        *,
        on_date: date,
        prefetched_reports: tuple | None = None,
    ) -> ReadinessSnapshot:
        health = self.repository.get_daily_health(user_id, on_date)
        used_latest_fallback = False
        if health is None:
            health = self.repository.latest_daily_health(user_id)
            used_latest_fallback = health is not None

        self_report = self._latest_self_report(user_id, on_date, prefetched=prefetched_reports)
        history = self._recent_history(user_id, on_date)
        result = self.calculator.assess(
            self._input(user_id, on_date, health, self_report), history=history,
        )
        return self._snapshot(
            user_id=user_id, requested_on=on_date, health=health,
            used_latest_fallback=used_latest_fallback,
            self_report=self_report, result=result,
        )

    def _recent_history(self, user_id: UUID, on_date: date) -> tuple[ReadinessResult, ...]:
        results: list[ReadinessResult] = []
        for days_ago in (1, 2, 3):
            past_date = on_date - timedelta(days=days_ago)
            past_health = self.repository.get_daily_health(user_id, past_date)
            if past_health is None:
                continue
            past_report = self._latest_self_report(user_id, past_date)
            results.append(self.calculator.assess(self._input(user_id, past_date, past_health, past_report)))
        return tuple(results)

    def _input(
        self,
        user_id: UUID,
        on_date: date,
        health: GarminDailyHealthRecord | None,
        self_report: SelfHealthReport | None,
    ) -> DailyReadinessInput:
        if health is None:
            return DailyReadinessInput(
                date=on_date,
                sleep_duration_minutes=self_report.sleep_minutes if self_report else None,
                self_report=_to_self_report(self_report),
            )
        return DailyReadinessInput.from_normalized_health(
            health,
            on_date=on_date,
            resting_heart_rate_baseline=self.repository.resting_heart_rate_baseline(user_id, before=on_date),
            self_report=_to_self_report(self_report),
        )

    def _latest_self_report(
        self, user_id: UUID, on_date: date, prefetched: tuple | None = None,
    ) -> SelfHealthReport | None:
        reports = prefetched if prefetched is not None else self.repository.list_self_reports(user_id, on_date)
        with_signal = tuple(
            r for r in reports
            if r.energy_score is not None or r.body_score is not None
            or r.life_load_score is not None or r.sleep_minutes is not None
        )
        return with_signal[-1] if with_signal else None

    @staticmethod
    def _snapshot(
        *,
        user_id: UUID,
        requested_on: date,
        health: GarminDailyHealthRecord | None,
        used_latest_fallback: bool,
        self_report: SelfHealthReport | None,
        result: ReadinessResult,
    ) -> ReadinessSnapshot:
        return ReadinessSnapshot(
            user_id=user_id,
            requested_on=requested_on,
            source_health_date=health.observed_on if health else None,
            used_latest_health_fallback=used_latest_fallback,
            score=result.score,
            band=result.band,
            confidence=result.confidence,
            contributions=result.contributions,
            rationale=result.rationale,
            missing_metrics=result.missing_metrics,
            data_lines=_readiness_data_lines(
                requested_on=requested_on, health=health,
                used_latest_fallback=used_latest_fallback, self_report=self_report,
            ),
            self_report_id=self_report.id if self_report else None,
        )


# ---------------------------------------------------------------------------
# Cycle tracking
# ---------------------------------------------------------------------------

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
        self, user_id: UUID, occurred_on: date, *, note: str | None = None,
    ) -> CycleEvent:
        if occurred_on > date.today():
            raise ValueError(f"Period start cannot be in the future: {occurred_on}")
        return self.repository.record(CycleEvent(
            id=uuid4(), user_id=user_id, event_type="period_start",
            occurred_on=occurred_on, note=note,
        ))

    def record_observation(
        self, user_id: UUID, occurred_on: date, *,
        note: str | None = None, symptoms: tuple[str, ...] = (),
    ) -> CycleEvent:
        return self.repository.record(CycleEvent(
            id=uuid4(), user_id=user_id, event_type="observation",
            occurred_on=occurred_on, note=note, symptoms=symptoms,
        ))

    def current_phase(self, user_id: UUID, today: date) -> str | None:
        last_period = self.repository.last_period_start(user_id)
        if last_period is None:
            return None
        cycle_day = (today - last_period.occurred_on).days + 1
        if cycle_day <= 5: return "menstruation"
        if cycle_day <= 13: return "follicular"
        if cycle_day <= 16: return "ovulation"
        if cycle_day <= 24: return "luteal"
        return "late_luteal"

    def get_status(self, user_id: UUID, today: date) -> str:
        last_period = self.repository.last_period_start(user_id)
        if last_period is None:
            return "Cycle: no period start recorded yet."
        cycle_day = (today - last_period.occurred_on).days + 1
        if cycle_day <= 5: phase = f"menstruation (day {cycle_day})"
        elif cycle_day <= 13: phase = f"follicular (day {cycle_day})"
        elif cycle_day <= 16: phase = f"ovulation window (day {cycle_day})"
        elif cycle_day <= 28: phase = f"luteal (day {cycle_day})"
        else: phase = f"late luteal / period due (day {cycle_day})"
        return f"Cycle: {phase}. Period started {last_period.occurred_on.isoformat()}."


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _bounded_int(value: int | None, *, low: int, high: int) -> int | None:
    if value is None:
        return None
    return value if low <= value <= high else None


def _band(score: int) -> ReadinessBand:
    if score >= 90: return ReadinessBand.STRONG
    if score >= 75: return ReadinessBand.READY
    if score >= 55: return ReadinessBand.STEADY
    return ReadinessBand.LOW


def _to_self_report(report: SelfHealthReport | None) -> SelfReport | None:
    if report is None:
        return None
    if report.energy_score is None and report.body_score is None and report.life_load_score is None:
        return None
    return SelfReport(
        energy=report.energy_score or 5,
        body=report.body_score or 5,
        life_load=report.life_load_score or 5,
        soreness=report.soreness_score or 3,
    )


def _readiness_data_lines(
    *,
    requested_on: date,
    health: GarminDailyHealthRecord | None,
    used_latest_fallback: bool,
    self_report: SelfHealthReport | None,
) -> tuple[str, ...]:
    lines: list[str] = []
    if health is None:
        lines.append("Garmin: no daily health data stored.")
    else:
        source = health.observed_on.isoformat()
        suffix = " (latest available fallback)" if used_latest_fallback else ""
        lines.append(f"Garmin source: {source}{suffix}.")
        if health.sleep_duration_minutes is not None or health.sleep_score is not None:
            parts = []
            if health.sleep_duration_minutes is not None:
                parts.append(f"{health.sleep_duration_minutes // 60}h {health.sleep_duration_minutes % 60}m")
            if health.sleep_score is not None:
                parts.append(f"score {health.sleep_score}")
            lines.append(f"Sleep: {', '.join(parts)}.")
        bb = health.body_battery_end or health.body_battery_maximum
        if bb is not None:
            lines.append(f"Body battery: {bb}.")
        if health.resting_heart_rate is not None:
            lines.append(f"Resting HR: {health.resting_heart_rate} bpm.")
        if health.hrv_last_night is not None:
            lines.append(f"HRV: last night {health.hrv_last_night:g}.")
        if health.average_stress is not None:
            lines.append(f"Stress: {health.average_stress}.")

    if self_report is None:
        lines.append("Self-report: missing.")
    else:
        bits = []
        if self_report.energy_score is not None: bits.append(f"energy {self_report.energy_score}/10")
        if self_report.body_score is not None: bits.append(f"body {self_report.body_score}/10")
        if self_report.life_load_score is not None: bits.append(f"life load {self_report.life_load_score}/10")
        if self_report.sleep_minutes is not None:
            bits.append(f"reported sleep {self_report.sleep_minutes // 60}h {self_report.sleep_minutes % 60}m")
        lines.append("Self-report: " + (", ".join(bits) if bits else "present but no scoring signal."))
    return tuple(lines)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


def _provenance(provenance: GarminHealthProvenance) -> dict[str, Any]:
    data = asdict(provenance)
    data["provider"] = provenance.provider.value
    data["sync_run_id"] = str(provenance.sync_run_id) if provenance.sync_run_id else None
    data["fetched_at"] = provenance.fetched_at.isoformat()
    return data


def _load_provenance(data: dict[str, Any]) -> GarminHealthProvenance:
    return GarminHealthProvenance(
        provider=HealthProvider(data.get("provider", HealthProvider.GARMIN.value)),
        sync_run_id=UUID(data["sync_run_id"]) if data.get("sync_run_id") else None,
        fetched_at=datetime.fromisoformat(data["fetched_at"]) if data.get("fetched_at") else datetime.now().astimezone(),
        worker_endpoint=data.get("worker_endpoint"),
    )


def _detail_section(
    raw_data: dict[str, Any], key: str, default: list[Any] | dict[str, Any],
) -> list[Any] | dict[str, Any]:
    value = raw_data.get(key)
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    return value if isinstance(value, dict) else default
