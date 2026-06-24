from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.health import (
    GarminActivityRecord,
    GarminDailyHealthRecord,
    GarminHealthProvenance,
    HealthProvider,
    HealthSyncKind,
    HealthSyncRun,
    HealthSyncStatus,
    SelfHealthReport,
)
from trellis.postgres import PostgresDatabase


class PostgresHealthRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def upsert_daily_health(
        self,
        record: GarminDailyHealthRecord,
    ) -> GarminDailyHealthRecord:
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
                        record.user_id,
                        record.observed_on,
                        record.steps,
                        record.calories,
                        record.distance_meters,
                        record.active_minutes,
                        record.resting_heart_rate,
                        record.average_heart_rate,
                        record.maximum_heart_rate,
                        record.sleep_duration_minutes,
                        record.sleep_score,
                        record.body_battery_maximum,
                        record.body_battery_minimum,
                        record.body_battery_end,
                        record.average_stress,
                        record.hrv_weekly_average,
                        record.hrv_last_night,
                        record.hrv_status,
                        _json(record.raw),
                        _json(_provenance(record.provenance)),
                        record.provenance.sync_run_id,
                        record.updated_at,
                    ),
                )
                return self._daily_health(cursor.fetchone())

    def get_daily_health(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> GarminDailyHealthRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM garmin_daily_health
                    WHERE user_id = %s AND observed_on = %s
                    """,
                    (user_id, observed_on),
                )
                row = cursor.fetchone()
                return self._daily_health(row) if row else None

    def resting_heart_rate_baseline(
        self,
        user_id: UUID,
        *,
        before,
        days: int = 60,
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

    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM garmin_daily_health
                    WHERE user_id = %s
                    ORDER BY observed_on DESC
                    LIMIT 1
                    """,
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
                        record.user_id,
                        record.activity_id,
                        record.name,
                        record.activity_type,
                        record.start_time_epoch_seconds,
                        record.duration_milliseconds,
                        record.calories,
                        record.average_heart_rate,
                        record.maximum_heart_rate,
                        record.distance_meters,
                        record.elevation_gain_meters,
                        record.elevation_loss_meters,
                        _json(record.raw),
                        _json(_provenance(record.provenance)),
                        record.provenance.sync_run_id,
                        record.updated_at,
                    ),
                )
                return self._activity(cursor.fetchone())

    def get_activity(
        self,
        user_id: UUID,
        activity_id: str,
    ) -> GarminActivityRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM garmin_activities
                    WHERE user_id = %s AND activity_id = %s
                    """,
                    (user_id, activity_id),
                )
                row = cursor.fetchone()
                return self._activity(row) if row else None

    def latest_activity(self, user_id: UUID) -> GarminActivityRecord | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM garmin_activities
                    WHERE user_id = %s
                    ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
                return self._activity(row) if row else None

    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                if activity_type:
                    cursor.execute(
                        """
                        SELECT * FROM garmin_activities
                        WHERE user_id = %s AND activity_type = %s
                        ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC
                        LIMIT %s
                        """,
                        (user_id, activity_type, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT * FROM garmin_activities
                        WHERE user_id = %s
                        ORDER BY start_time_epoch_seconds DESC NULLS LAST, updated_at DESC
                        LIMIT %s
                        """,
                        (user_id, limit),
                    )
                return tuple(self._activity(row) for row in cursor.fetchall())

    def latest_activities_with_detail(
        self,
        user_id: UUID,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        a.activity_id,
                        a.name,
                        a.activity_type,
                        a.start_time_epoch_seconds,
                        a.duration_milliseconds,
                        a.calories,
                        a.distance_meters,
                        a.elevation_gain_meters,
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
        self,
        *,
        user_id: UUID,
        activity_id: str,
        raw_data: dict[str, Any],
        sync_run_id: UUID | None,
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
                        user_id,
                        activity_id,
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
                            user_id,
                            activity_id,
                        ),
                    )

    def latest_activity_detail(
        self,
        user_id: UUID,
        *,
        activity_type: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                if activity_type:
                    cursor.execute(
                        """
                        SELECT details.*
                        FROM garmin_activity_details details
                        JOIN garmin_activities activities
                          ON activities.user_id = details.user_id
                         AND activities.activity_id = details.activity_id
                        WHERE details.user_id = %s
                          AND activities.activity_type = %s
                        ORDER BY activities.start_time_epoch_seconds DESC NULLS LAST,
                                 details.updated_at DESC
                        LIMIT 1
                        """,
                        (user_id, activity_type),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT details.*
                        FROM garmin_activity_details details
                        JOIN garmin_activities activities
                          ON activities.user_id = details.user_id
                         AND activities.activity_id = details.activity_id
                        WHERE details.user_id = %s
                        ORDER BY activities.start_time_epoch_seconds DESC NULLS LAST,
                                 details.updated_at DESC
                        LIMIT 1
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
                    """
                    SELECT * FROM garmin_activity_details
                    WHERE user_id = %s AND activity_id = %s
                    """,
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
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                    )
                    RETURNING *
                    """,
                    (
                        report.id,
                        report.user_id,
                        report.observed_on,
                        report.energy_score,
                        report.life_load_score,
                        report.sleep_minutes,
                        report.body_score,
                        report.soreness_score,
                        report.note,
                        report.source_capture_id,
                        _json(report.raw),
                        report.reported_at,
                    ),
                )
                return self._self_report(cursor.fetchone())

    def list_self_reports(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM health_self_reports
                    WHERE user_id = %s AND observed_on = %s
                    ORDER BY reported_at
                    """,
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
                        run.id,
                        run.user_id,
                        run.provider.value,
                        run.kind.value,
                        run.status.value,
                        run.start_date,
                        run.end_date,
                        run.started_at,
                        run.records_upserted,
                        _json(run.metadata),
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
                    SET status = %s,
                        completed_at = %s,
                        records_upserted = %s,
                        error = %s,
                        metadata = %s::jsonb
                    WHERE id = %s
                    """,
                    (
                        run.status.value,
                        run.completed_at,
                        run.records_upserted,
                        run.error,
                        _json(run.metadata),
                        run.id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise LookupError(run.id)
        return run

    def get_sync(self, sync_run_id: UUID) -> HealthSyncRun | None:
        with self.database.connect() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM health_sync_runs WHERE id = %s",
                    (sync_run_id,),
                )
                row = cursor.fetchone()
                return self._sync(row) if row else None

    @staticmethod
    def _daily_health(row: dict[str, Any]) -> GarminDailyHealthRecord:
        return GarminDailyHealthRecord(
            user_id=row["user_id"],
            observed_on=row["observed_on"],
            steps=row["steps"],
            calories=row["calories"],
            distance_meters=float(row["distance_meters"])
            if row["distance_meters"] is not None
            else None,
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
            hrv_weekly_average=float(row["hrv_weekly_average"])
            if row["hrv_weekly_average"] is not None
            else None,
            hrv_last_night=float(row["hrv_last_night"])
            if row["hrv_last_night"] is not None
            else None,
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
            duration_milliseconds=float(row["duration_milliseconds"])
            if row["duration_milliseconds"] is not None
            else None,
            calories=row["calories"],
            average_heart_rate=row["average_heart_rate"],
            maximum_heart_rate=row["maximum_heart_rate"],
            distance_meters=float(row["distance_meters"])
            if row["distance_meters"] is not None
            else None,
            elevation_gain_meters=float(row["elevation_gain_meters"])
            if row["elevation_gain_meters"] is not None
            else None,
            elevation_loss_meters=float(row["elevation_loss_meters"])
            if row["elevation_loss_meters"] is not None
            else None,
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
    def _sync(row: dict[str, Any]) -> HealthSyncRun:
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


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


def _detail_section(
    raw_data: dict[str, Any],
    key: str,
    default: list[Any] | dict[str, Any],
) -> list[Any] | dict[str, Any]:
    value = raw_data.get(key)
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    return value if isinstance(value, dict) else default


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
        fetched_at=datetime.fromisoformat(data["fetched_at"])
        if data.get("fetched_at")
        else datetime.now().astimezone(),
        worker_endpoint=data.get("worker_endpoint"),
    )
