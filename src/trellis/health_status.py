from __future__ import annotations

from typing import Protocol
from uuid import UUID

from trellis.garmin_setup import GarminConnectionStatus
from trellis.health import GarminActivityRecord, GarminDailyHealthRecord


class HealthStatusRepository(Protocol):
    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None:
        ...

    def latest_activity(self, user_id: UUID) -> GarminActivityRecord | None:
        ...

    def latest_activity_detail(
        self,
        user_id: UUID,
        *,
        activity_type: str | None = None,
    ) -> dict | None:
        ...


class HealthConnectionRepository(Protocol):
    def status(self, user_id: UUID) -> GarminConnectionStatus:
        ...


class HealthStatusService:
    def __init__(
        self,
        health_repository: HealthStatusRepository,
        connection_repository: HealthConnectionRepository,
    ):
        self.health_repository = health_repository
        self.connection_repository = connection_repository

    def telegram_summary(self, user_id: UUID, query: str = "") -> str:
        connection = self.connection_repository.status(user_id)
        if not connection.is_connected:
            return "Garmin is not connected yet."
        if _is_split_query(query):
            return self._split_summary(user_id, connection)
        if _is_activity_query(query):
            return self._activity_summary(user_id, connection)

        latest = self.health_repository.latest_daily_health(user_id)
        if latest is None:
            if connection.last_error:
                return f"Garmin is connected, but no health data is stored yet. Last error: {connection.last_error}"
            return "Garmin is connected, but no health data is stored yet. Run a sync first."

        lines = [f"Latest Garmin data: {latest.observed_on.isoformat()}"]
        _append(lines, "Sleep", _sleep(latest))
        _append(lines, "Sleep score", latest.sleep_score)
        _append(lines, "Body battery", latest.body_battery_end or latest.body_battery_maximum)
        _append(lines, "Resting HR", _bpm(latest.resting_heart_rate))
        _append(lines, "HRV", _ms(latest.hrv_last_night))
        _append(lines, "Stress", latest.average_stress)
        _append(lines, "Steps", latest.steps)
        if connection.last_sync_at:
            lines.append(f"Last sync: {connection.last_sync_at}")
        if connection.last_error:
            lines.append(f"Last sync error: {connection.last_error}")
        return "\n".join(lines)

    def _split_summary(
        self,
        user_id: UUID,
        connection: GarminConnectionStatus,
    ) -> str:
        activity = self.health_repository.latest_activity(user_id)
        detail = self.health_repository.latest_activity_detail(
            user_id,
            activity_type="running",
        )
        if detail is None:
            lines = ["No Garmin workout segments are stored yet."]
            if connection.last_sync_at:
                lines.append(f"Last sync: {connection.last_sync_at}")
            lines.append("Run Garmin sync with activity details first.")
            return "\n".join(lines)

        segments = _extract_segment_rows(detail)
        heading = "Latest run workout segments"
        if activity is not None:
            heading = f"Latest run workout segments: {activity.name}"
        lines = [heading]
        if not segments:
            lines.append("Garmin returned detail data, but no readable workout segments yet.")
        else:
            for index, segment in enumerate(segments[:8], 1):
                lines.append(f"{index}. {segment}")
            if len(segments) > 8:
                lines.append(f"... {len(segments) - 8} more segments stored")
        if connection.last_sync_at:
            lines.append(f"Last sync: {connection.last_sync_at}")
        return "\n".join(lines)

    def _activity_summary(
        self,
        user_id: UUID,
        connection: GarminConnectionStatus,
    ) -> str:
        activity = self.health_repository.latest_activity(user_id)
        if activity is None:
            lines = ["No Garmin activities are stored yet."]
            if connection.last_sync_at:
                lines.append(f"Last sync: {connection.last_sync_at}")
            lines.append("Try a longer Garmin sync window if you expected an activity.")
            return "\n".join(lines)

        lines = ["Most recent Garmin activity"]
        _append(lines, "Name", activity.name)
        _append(lines, "Type", activity.activity_type)
        _append(lines, "Distance", _km(activity.distance_meters))
        _append(lines, "Duration", _duration(activity.duration_milliseconds))
        _append(lines, "Average HR", _bpm(activity.average_heart_rate))
        _append(lines, "Max HR", _bpm(activity.maximum_heart_rate))
        _append(lines, "Calories", activity.calories)
        if connection.last_sync_at:
            lines.append(f"Last sync: {connection.last_sync_at}")
        return "\n".join(lines)


def _append(lines: list[str], label: str, value: object | None) -> None:
    if value is not None:
        lines.append(f"{label}: {value}")


def _sleep(record: GarminDailyHealthRecord) -> str | None:
    if record.sleep_duration_minutes is None:
        return None
    hours, minutes = divmod(record.sleep_duration_minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _bpm(value: int | None) -> str | None:
    return f"{value} bpm" if value is not None else None


def _ms(value: float | None) -> str | None:
    return f"{value:g} ms" if value is not None else None


def _km(value: float | None) -> str | None:
    return f"{value / 1000:.2f} km" if value is not None else None


def _duration(value: float | None) -> str | None:
    if value is None:
        return None
    seconds = round(value / 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes}m {seconds:02d}s"


def _is_split_query(query: str) -> bool:
    lowered = query.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "split",
            "splits",
            "lap",
            "laps",
            "interval",
            "intervals",
        )
    )


def _is_activity_query(query: str) -> bool:
    lowered = query.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "activity",
            "activities",
            "run",
            "workout",
            "exercise",
        )
    )


def _extract_segment_rows(detail: dict) -> list[str]:
    rows = _meaningful_split_rows(detail)
    seen: set[str] = set()
    rendered: list[str] = []
    for row in rows:
        text = _render_split(row)
        if text and text not in seen:
            rendered.append(text)
            seen.add(text)
    return rendered


def _meaningful_split_rows(detail: dict) -> list[dict]:
    typed_rows = _flatten_split_dicts(detail.get("typed_splits"))
    interval_rows = [
        row
        for row in typed_rows
        if _split_type(row).startswith("INTERVAL")
        and (_first_number(row, "duration", "movingDuration", "elapsedDuration") or 0) >= 10
    ]
    if interval_rows:
        return interval_rows

    run_rows = [
        row
        for row in typed_rows
        if _split_type(row).endswith("RUN")
        and (_first_number(row, "distance", "totalDistance", "splitDistance") or 0) >= 100
        and (_first_number(row, "duration", "movingDuration", "elapsedDuration") or 0) >= 45
    ]
    if run_rows:
        return run_rows

    rows: list[dict] = []
    for candidate in (detail.get("splits"), detail.get("split_summaries")):
        rows.extend(_flatten_split_dicts(candidate))
    return [
        row
        for row in rows
        if (_first_number(row, "distance", "totalDistance", "splitDistance") or 0) >= 100
        and (_first_number(row, "duration", "movingDuration", "elapsedDuration") or 0) >= 45
    ]


def _flatten_split_dicts(value: object) -> list[dict]:
    if isinstance(value, list):
        rows: list[dict] = []
        for item in value:
            rows.extend(_flatten_split_dicts(item))
        return rows
    if isinstance(value, dict):
        if any(key in value for key in ("distance", "duration", "averageSpeed", "averageHR")):
            return [value]
        rows = []
        for item in value.values():
            rows.extend(_flatten_split_dicts(item))
        return rows
    return []


def _render_split(row: dict) -> str | None:
    distance = _first_number(row, "distance", "totalDistance", "splitDistance")
    duration = _first_number(row, "duration", "movingDuration", "elapsedDuration")
    average_hr = _first_number(row, "averageHR", "averageHeartRate", "avgHeartRate")
    pace = _pace(distance, duration)
    parts = []
    split_type = _split_type(row)
    if split_type:
        parts.append(_friendly_split_type(split_type))
    if distance is not None:
        parts.append(f"{distance / 1000:.2f} km")
    if duration is not None:
        parts.append(_duration(duration * 1000))
    if pace:
        parts.append(pace)
    if average_hr is not None:
        parts.append(f"{average_hr:g} bpm")
    return " | ".join(part for part in parts if part) or None


def _split_type(row: dict) -> str:
    value = row.get("type") or row.get("splitType")
    return value if isinstance(value, str) else ""


def _friendly_split_type(value: str) -> str:
    lowered = value.casefold()
    if lowered == "interval_active":
        return "active interval"
    if lowered == "interval_rest":
        return "rest interval"
    if lowered == "rwd_run":
        return "run segment"
    return lowered.replace("_", " ")


def _first_number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _pace(distance_meters: float | None, duration_seconds: float | None) -> str | None:
    if not distance_meters or not duration_seconds:
        return None
    if distance_meters <= 0:
        return None
    seconds_per_km = duration_seconds / (distance_meters / 1000)
    minutes, seconds = divmod(round(seconds_per_km), 60)
    return f"{minutes}:{seconds:02d}/km"
