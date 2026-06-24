"""HTTP client and normalization boundary for the Garmin health worker."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Mapping, Protocol

from .models import (
    GarminActivity,
    GarminActivityDetail,
    GarminAuthResult,
    GarminAuthStatus,
    GarminDailyHealth,
)


class GarminClientError(RuntimeError):
    """Base error for the Garmin worker boundary."""


class GarminConfigurationError(GarminClientError):
    """The client cannot be used because required configuration is missing."""


class GarminTransportError(GarminClientError):
    """The worker could not be reached or returned unreadable JSON."""


class GarminHTTPError(GarminClientError):
    """The worker returned a non-success HTTP status."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Garmin worker returned HTTP {status_code}: {detail}")


class GarminResponseError(GarminClientError):
    """The worker response did not satisfy its expected contract."""


class JsonTransport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout: float,
    ) -> Any:
        """Send a request and return its decoded JSON body."""


class UrllibJsonTransport:
    """Standard-library JSON transport used by the production client."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout: float,
    ) -> Any:
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            raise GarminHTTPError(error.code, _error_detail(payload)) from error
        except (TimeoutError, socket.timeout) as error:
            raise GarminTransportError(
                f"Garmin worker timed out after {timeout:g} seconds"
            ) from error
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            raise GarminTransportError(f"Could not reach Garmin worker: {reason}") from error

        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise GarminTransportError("Garmin worker returned invalid JSON") from error


class GarminClient:
    """Typed client for Allerac's stateless Garmin health worker."""

    def __init__(
        self,
        base_url: str,
        worker_secret: str,
        *,
        timeout: float = 30.0,
        transport: JsonTransport | None = None,
    ):
        if not base_url.strip():
            raise GarminConfigurationError("Garmin worker base URL is required")
        if not worker_secret.strip():
            raise GarminConfigurationError("Garmin worker secret is required")
        if timeout <= 0:
            raise GarminConfigurationError("Garmin worker timeout must be positive")

        self._base_url = base_url.rstrip("/")
        self._worker_secret = worker_secret
        self._timeout = timeout
        self._transport = transport or UrllibJsonTransport()

    def connect(self, email: str, password: str) -> GarminAuthResult:
        if not email.strip() or not password:
            raise ValueError("Garmin email and password are required")
        response = self._post("/connect", {"email": email, "password": password})
        return _normalize_auth(response, endpoint="/connect")

    def complete_mfa(self, session_id: str, mfa_code: str) -> GarminAuthResult:
        if not session_id.strip() or not mfa_code.strip():
            raise ValueError("Garmin MFA session ID and code are required")
        response = self._post(
            "/mfa",
            {"session_id": session_id, "mfa_code": mfa_code},
        )
        result = _normalize_auth(response, endpoint="/mfa")
        if result.status is not GarminAuthStatus.SUCCESS:
            raise GarminResponseError("/mfa did not return a successful session")
        return result

    def sync(
        self,
        session_dump: str,
        start_date: date,
        end_date: date,
    ) -> tuple[GarminDailyHealth, ...]:
        _require_session(session_dump)
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        response = _mapping(
            self._post(
                "/sync",
                {
                    "session_dump": session_dump,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            ),
            "/sync response",
        )
        metrics = _list(response.get("metrics"), "/sync.metrics")
        return tuple(
            _normalize_health(item, fallback_date=None, location=f"/sync.metrics[{index}]")
            for index, item in enumerate(metrics)
        )

    def activities(
        self,
        session_dump: str,
        *,
        limit: int = 10,
        on_date: date | None = None,
    ) -> tuple[GarminActivity, ...]:
        _require_session(session_dump)
        if limit < 1:
            raise ValueError("Activity limit must be at least 1")
        payload: dict[str, Any] = {"session_dump": session_dump, "limit": limit}
        if on_date is not None:
            payload["date"] = on_date.isoformat()
        response = _mapping(self._post("/activities", payload), "/activities response")
        activities = _list(response.get("activities"), "/activities.activities")
        return tuple(
            _normalize_activity(item, location=f"/activities.activities[{index}]")
            for index, item in enumerate(activities)
        )

    def activity_detail(
        self,
        session_dump: str,
        activity_id: str,
    ) -> GarminActivityDetail:
        _require_session(session_dump)
        if not activity_id.strip():
            raise ValueError("Activity ID is required")
        response = _mapping(
            self._post(
                "/activity-detail",
                {"session_dump": session_dump, "activity_id": activity_id},
            ),
            "/activity-detail response",
        )
        return _normalize_activity_detail(response, location="/activity-detail response")

    def daily_health(
        self,
        session_dump: str,
        on_date: date,
    ) -> GarminDailyHealth:
        _require_session(session_dump)
        response = self._post(
            "/daily-health",
            {"session_dump": session_dump, "date": on_date.isoformat()},
        )
        return _normalize_health(
            response,
            fallback_date=on_date,
            location="/daily-health response",
        )

    def _post(self, path: str, body: Mapping[str, Any]) -> Any:
        return self._transport.request_json(
            "POST",
            f"{self._base_url}{path}",
            headers={
                "Content-Type": "application/json",
                "X-Worker-Secret": self._worker_secret,
            },
            body=body,
            timeout=self._timeout,
        )


def _normalize_auth(value: Any, *, endpoint: str) -> GarminAuthResult:
    payload = _mapping(value, f"{endpoint} response")
    status = payload.get("status")
    if status == GarminAuthStatus.SUCCESS.value:
        session_dump = _required_string(payload.get("session_dump"), f"{endpoint}.session_dump")
        return GarminAuthResult(
            status=GarminAuthStatus.SUCCESS,
            session_dump=session_dump,
        )
    if status == GarminAuthStatus.MFA_REQUIRED.value:
        session_id = _required_string(payload.get("session_id"), f"{endpoint}.session_id")
        return GarminAuthResult(
            status=GarminAuthStatus.MFA_REQUIRED,
            mfa_session_id=session_id,
        )
    raise GarminResponseError(f"{endpoint}.status has unsupported value {status!r}")


def _normalize_health(
    value: Any,
    *,
    fallback_date: date | None,
    location: str,
) -> GarminDailyHealth:
    payload = dict(_mapping(value, location))
    raw_date = payload.get("date")
    metric_date = _date(raw_date, f"{location}.date") if raw_date is not None else fallback_date
    if metric_date is None:
        raise GarminResponseError(f"{location}.date is required")

    return GarminDailyHealth(
        date=metric_date,
        steps=_integer(payload.get("steps"), f"{location}.steps"),
        calories=_integer(payload.get("calories"), f"{location}.calories"),
        distance_meters=_number(payload.get("distance_meters"), f"{location}.distance_meters"),
        active_minutes=_integer(payload.get("active_minutes"), f"{location}.active_minutes"),
        floors_climbed=_number(payload.get("floors_climbed"), f"{location}.floors_climbed"),
        resting_heart_rate=_integer(payload.get("resting_hr"), f"{location}.resting_hr"),
        average_heart_rate=_integer(payload.get("avg_hr"), f"{location}.avg_hr"),
        maximum_heart_rate=_integer(payload.get("max_hr"), f"{location}.max_hr"),
        sleep_duration_minutes=_integer(
            payload.get("sleep_duration_minutes"),
            f"{location}.sleep_duration_minutes",
        ),
        sleep_deep_minutes=_integer(
            payload.get("sleep_deep_minutes"),
            f"{location}.sleep_deep_minutes",
        ),
        sleep_light_minutes=_integer(
            payload.get("sleep_light_minutes"),
            f"{location}.sleep_light_minutes",
        ),
        sleep_rem_minutes=_integer(
            payload.get("sleep_rem_minutes"),
            f"{location}.sleep_rem_minutes",
        ),
        sleep_awake_minutes=_integer(
            payload.get("sleep_awake_minutes"),
            f"{location}.sleep_awake_minutes",
        ),
        sleep_score=_integer(payload.get("sleep_score"), f"{location}.sleep_score"),
        body_battery_maximum=_integer(
            payload.get("body_battery_max"),
            f"{location}.body_battery_max",
        ),
        body_battery_minimum=_integer(
            payload.get("body_battery_min"),
            f"{location}.body_battery_min",
        ),
        body_battery_end=_integer(
            payload.get("body_battery_end"),
            f"{location}.body_battery_end",
        ),
        body_battery_charged=_integer(
            payload.get("body_battery_charged"),
            f"{location}.body_battery_charged",
        ),
        body_battery_drained=_integer(
            payload.get("body_battery_drained"),
            f"{location}.body_battery_drained",
        ),
        average_stress=_integer(payload.get("stress_avg"), f"{location}.stress_avg"),
        maximum_stress=_integer(payload.get("stress_max"), f"{location}.stress_max"),
        stress_rest_minutes=_integer(
            payload.get("stress_rest_duration_minutes"),
            f"{location}.stress_rest_duration_minutes",
        ),
        hrv_weekly_average=_number(
            payload.get("hrv_weekly_avg"),
            f"{location}.hrv_weekly_avg",
        ),
        hrv_last_night=_number(
            payload.get("hrv_last_night"),
            f"{location}.hrv_last_night",
        ),
        hrv_status=_optional_string(payload.get("hrv_status"), f"{location}.hrv_status"),
        raw=payload,
    )


def _normalize_activity(value: Any, *, location: str) -> GarminActivity:
    payload = dict(_mapping(value, location))
    return GarminActivity(
        activity_id=_identifier(payload.get("activityId"), f"{location}.activityId"),
        name=_required_string(payload.get("activityName"), f"{location}.activityName"),
        activity_type=_required_string(
            payload.get("activityType"),
            f"{location}.activityType",
        ),
        start_time_epoch_seconds=_integer(
            payload.get("startTimeInSeconds"),
            f"{location}.startTimeInSeconds",
        ),
        duration_milliseconds=_duration_milliseconds(
            payload.get("duration"),
            f"{location}.duration",
        ),
        calories=_integer(payload.get("calories"), f"{location}.calories"),
        average_heart_rate=_integer(
            payload.get("avgHeartRate"),
            f"{location}.avgHeartRate",
        ),
        maximum_heart_rate=_integer(
            payload.get("maxHeartRate"),
            f"{location}.maxHeartRate",
        ),
        distance_meters=_number(payload.get("distance"), f"{location}.distance"),
        elevation_gain_meters=_number(
            payload.get("elevationGain"),
            f"{location}.elevationGain",
        ),
        elevation_loss_meters=_number(
            payload.get("elevationLoss"),
            f"{location}.elevationLoss",
        ),
        active_sets=_integer(payload.get("activeSets"), f"{location}.activeSets"),
        total_exercise_repetitions=_integer(
            payload.get("totalExerciseReps"),
            f"{location}.totalExerciseReps",
        ),
        summarized_exercise_sets=payload.get("summarizedExerciseSets"),
        raw=payload,
    )


def _normalize_activity_detail(value: Any, *, location: str) -> GarminActivityDetail:
    payload = dict(_mapping(value, location))
    return GarminActivityDetail(
        activity_id=_identifier(payload.get("activityId"), f"{location}.activityId"),
        raw=payload,
    )


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GarminResponseError(f"{location} must be a JSON object")
    return value


def _duration_milliseconds(value: Any, location: str) -> float | None:
    duration = _number(value, location)
    if duration is None:
        return None
    # garminconnect activity summaries return duration in seconds. Older tests
    # and some wrappers may provide milliseconds, so keep large values unchanged.
    if duration <= 24 * 60 * 60:
        return duration * 1000
    return duration


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise GarminResponseError(f"{location} must be a JSON array")
    return value


def _required_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GarminResponseError(f"{location} must be a non-empty string")
    return value


def _optional_string(value: Any, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GarminResponseError(f"{location} must be a string or null")
    return value


def _identifier(value: Any, location: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise GarminResponseError(f"{location} must be a string or integer")
    result = str(value).strip()
    if not result:
        raise GarminResponseError(f"{location} must not be empty")
    return result


def _integer(value: Any, location: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise GarminResponseError(f"{location} must be an integer or null")
    return int(value)


def _number(value: Any, location: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GarminResponseError(f"{location} must be numeric or null")
    return float(value)


def _date(value: Any, location: str) -> date:
    if not isinstance(value, str):
        raise GarminResponseError(f"{location} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise GarminResponseError(f"{location} must be an ISO date") from error


def _require_session(session_dump: str) -> None:
    if not session_dump.strip():
        raise ValueError("Garmin session dump is required")


def _error_detail(payload: str) -> str:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload.strip()[:300] or "No error detail"
    if isinstance(parsed, Mapping):
        detail = parsed.get("detail") or parsed.get("error")
        if isinstance(detail, str) and detail:
            return detail[:300]
    return "Worker request failed"
