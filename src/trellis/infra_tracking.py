"""Synced Garmin health records — daily health, activities, activity details,
and the sync-run bookkeeping around them. (The June-era deterministic readiness
calculator and cycle service that used to live here had no callers and were
removed 5 Aug 2026 — cycle-day tracking lives in Sense; the Watcher builds its
own verifiers to purpose.)"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
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
    def daily_health_since(self, user_id: UUID, *, since: date) -> list[GarminDailyHealthRecord]:
        """All synced daily-health rows from `since` on — the Watcher's frame."""
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_daily_health WHERE user_id = %s"
                    " AND observed_on >= %s ORDER BY observed_on",
                    (user_id, since),
                )
                return [self._daily_health(r) for r in cursor.fetchall()]

    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None: ...
    def upsert_activity(self, record: GarminActivityRecord) -> GarminActivityRecord: ...
    def latest_activities(self, user_id: UUID, *, limit: int, activity_type: str | None = None) -> tuple[GarminActivityRecord, ...]: ...
    def activities_since(self, user_id: UUID, *, since: date) -> list[GarminActivityRecord]:
        """All synced activities from `since` on — the Watcher's training frame."""
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_activities WHERE user_id = %s"
                    " AND start_time_epoch_seconds >= %s"
                    " ORDER BY start_time_epoch_seconds",
                    (user_id, int(datetime.combine(since, datetime.min.time(),
                                                   tzinfo=timezone.utc).timestamp())),
                )
                return [self._activity(r) for r in cursor.fetchall()]

    def upsert_activity_detail(self, *, user_id: UUID, activity_id: str, raw_data: dict[str, Any], sync_run_id: UUID | None) -> None: ...
    def start_sync(self, run: HealthSyncRun) -> HealthSyncRun: ...
    def finish_sync(self, run: HealthSyncRun) -> HealthSyncRun: ...


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

    def daily_health_since(self, user_id: UUID, *, since: date) -> list[GarminDailyHealthRecord]:
        """All synced daily-health rows from `since` on — the Watcher's frame."""
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_daily_health WHERE user_id = %s"
                    " AND observed_on >= %s ORDER BY observed_on",
                    (user_id, since),
                )
                return [self._daily_health(r) for r in cursor.fetchall()]

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

    def activities_since(self, user_id: UUID, *, since: date) -> list[GarminActivityRecord]:
        """All synced activities from `since` on — the Watcher's training frame."""
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM garmin_activities WHERE user_id = %s"
                    " AND start_time_epoch_seconds >= %s"
                    " ORDER BY start_time_epoch_seconds",
                    (user_id, int(datetime.combine(since, datetime.min.time(),
                                                   tzinfo=timezone.utc).timestamp())),
                )
                return [self._activity(r) for r in cursor.fetchall()]

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
# Private helpers
# ---------------------------------------------------------------------------

def _bounded_int(value: int | None, *, low: int, high: int) -> int | None:
    if value is None:
        return None
    return value if low <= value <= high else None


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
