"""Garmin integration — API client, sync, push, connection management.

Consolidates: garmin/models.py, garmin/client.py, garmin_push.py,
              garmin_setup.py, garmin_sync.py
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import UUID

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Garmin API models (raw wire format from the health worker)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Health worker HTTP client
# ---------------------------------------------------------------------------

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
            url, data=encoded, headers=dict(headers), method=method,
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
    """Typed client for the stateless Garmin health worker."""

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
        response = self._post("/mfa", {"session_id": session_id, "mfa_code": mfa_code})
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
            self._post("/sync", {
                "session_dump": session_dump,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }),
            "/sync response",
        )
        metrics = _list(response.get("metrics"), "/sync.metrics")
        return tuple(
            _normalize_health(item, fallback_date=None, location=f"/sync.metrics[{i}]")
            for i, item in enumerate(metrics)
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
            _normalize_activity(item, location=f"/activities.activities[{i}]")
            for i, item in enumerate(activities)
        )

    def activity_detail(self, session_dump: str, activity_id: str) -> GarminActivityDetail:
        _require_session(session_dump)
        if not activity_id.strip():
            raise ValueError("Activity ID is required")
        response = _mapping(
            self._post("/activity-detail", {
                "session_dump": session_dump,
                "activity_id": activity_id,
            }),
            "/activity-detail response",
        )
        return _normalize_activity_detail(response, location="/activity-detail response")

    def daily_health(self, session_dump: str, on_date: date) -> GarminDailyHealth:
        _require_session(session_dump)
        response = self._post(
            "/daily-health", {"session_dump": session_dump, "date": on_date.isoformat()}
        )
        return _normalize_health(response, fallback_date=on_date, location="/daily-health response")

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


# ---------------------------------------------------------------------------
# Push service (write workouts to Garmin Connect via garminconnect library)
# ---------------------------------------------------------------------------

class GarminDirectService:
    """Write operations to Garmin Connect using the stored session dump."""

    def __init__(self, connection_repository: _DirectConnectionRepo):
        self.connection_repository = connection_repository

    def _connect(self, user_id: UUID):
        dump = self.connection_repository.get_session_dump(user_id)
        if not dump:
            raise RuntimeError("Garmin not connected. Use /garmin_setup to connect.")
        try:
            import garminconnect  # noqa: F401
            client = garminconnect.Garmin()
            client.garth.loads(dump)
            return client
        except Exception as exc:
            raise RuntimeError(f"Failed to load Garmin session: {exc}") from exc

    def push_workout(self, user_id: UUID, workout_json: str) -> str:
        try:
            client = self._connect(user_id)
            result = client.add_workout(workout_json)
            return str(result.get("workoutId", result))
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to push workout to Garmin: {exc}") from exc

    def schedule_workout(self, user_id: UUID, workout_id: str, on_date: date) -> None:
        try:
            client = self._connect(user_id)
            client.schedule_workout(workout_id, on_date.isoformat())
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to schedule workout {workout_id}: {exc}") from exc

    def list_workouts(self, user_id: UUID, *, limit: int = 20) -> list[dict]:
        try:
            client = self._connect(user_id)
            result = client.get_workouts(0, limit)
            return result if isinstance(result, list) else []
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to list Garmin workouts: {exc}") from exc

    def delete_workout(self, user_id: UUID, workout_id: str) -> None:
        try:
            client = self._connect(user_id)
            client.delete_workout(workout_id)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to delete workout {workout_id}: {exc}") from exc


class _DirectConnectionRepo(Protocol):
    def get_session_dump(self, user_id: UUID) -> str | None: ...


class GarminActivityReader:
    """Recent RUNNING activities for the coach's run log — recent + small, via the
    health worker. NOT bulk history (that's the CSV baseline path)."""

    def __init__(self, connection_repository: _DirectConnectionRepo, client: "GarminClient"):
        self._connections = connection_repository
        self._client = client

    def recent_running_activities(self, user_id: UUID, *, limit: int = 20) -> list[GarminActivity]:
        dump = self._connections.get_session_dump(user_id)
        if not dump:
            raise RuntimeError("Garmin not connected. Use /garmin_setup to connect.")
        activities = self._client.activities(dump, limit=max(1, min(limit, 50)))
        return [a for a in activities if "run" in (a.activity_type or "").lower()]


# ---------------------------------------------------------------------------
# Connection management (setup + status)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GarminConnectionStatus:
    is_connected: bool
    sync_enabled: bool
    last_sync_at: object | None = None
    last_error: str | None = None


class PostgresGarminConnectionRepository:
    def __init__(self, database, secret_key: str):
        if not secret_key.strip():
            raise ValueError("Trellis secret key is required")
        self.database = database
        self.secret_key = secret_key

    def save_connected(self, user_id: UUID, *, email: str, session_dump: str) -> None:
        if not email.strip():
            raise ValueError("Garmin email is required")
        if not session_dump.strip():
            raise ValueError("Garmin session dump is required")
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO garmin_connections (
                        user_id, email_encrypted, session_dump_encrypted,
                        is_connected, last_error
                    ) VALUES (
                        %s,
                        encode(pgp_sym_encrypt(%s, %s), 'base64'),
                        encode(pgp_sym_encrypt(%s, %s), 'base64'),
                        true,
                        NULL
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        email_encrypted = EXCLUDED.email_encrypted,
                        session_dump_encrypted = EXCLUDED.session_dump_encrypted,
                        is_connected = true,
                        last_error = NULL,
                        updated_at = NOW()
                    """,
                    (user_id, email, self.secret_key, session_dump, self.secret_key),
                )

    def get_session_dump(self, user_id: UUID) -> str | None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pgp_sym_decrypt(
                        decode(session_dump_encrypted, 'base64'), %s
                    )
                    FROM garmin_connections
                    WHERE user_id = %s
                      AND is_connected = true
                      AND session_dump_encrypted IS NOT NULL
                    """,
                    (self.secret_key, user_id),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def status(self, user_id: UUID) -> GarminConnectionStatus:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT is_connected, sync_enabled, last_sync_at, last_error"
                    " FROM garmin_connections WHERE user_id = %s",
                    (user_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return GarminConnectionStatus(is_connected=False, sync_enabled=False)
        return GarminConnectionStatus(
            is_connected=row[0], sync_enabled=row[1],
            last_sync_at=row[2], last_error=row[3],
        )

    def mark_sync_success(self, user_id: UUID, synced_at: datetime) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE garmin_connections SET last_sync_at = %s, last_error = NULL,"
                    " updated_at = NOW() WHERE user_id = %s",
                    (synced_at, user_id),
                )

    def mark_sync_failure(self, user_id: UUID, error: str) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE garmin_connections SET last_error = %s, updated_at = NOW()"
                    " WHERE user_id = %s",
                    (error[:2000], user_id),
                )

    def get_last_sync_at(self, user_id: UUID) -> datetime | None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT last_sync_at FROM garmin_connections WHERE user_id = %s",
                    (user_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Sync service
# ---------------------------------------------------------------------------

class GarminConnectionRepository(Protocol):
    def get_session_dump(self, user_id: UUID) -> str | None: ...
    def get_last_sync_at(self, user_id: UUID) -> datetime | None: ...
    def mark_sync_success(self, user_id: UUID, synced_at: datetime) -> None: ...
    def mark_sync_failure(self, user_id: UUID, error: str) -> None: ...


class GarminSyncClient(Protocol):
    def sync(self, session_dump: str, start_date: date, end_date: date) -> tuple[GarminDailyHealth, ...]: ...
    def activities(self, session_dump: str, *, limit: int = 10, on_date: date | None = None) -> tuple[GarminActivity, ...]: ...
    def activity_detail(self, session_dump: str, activity_id: str) -> GarminActivityDetail: ...


@dataclass(frozen=True)
class GarminSyncSummary:
    daily_health_records: int
    activity_records: int
    activity_detail_records: int
    start_date: date
    end_date: date


class GarminSyncService:
    def __init__(
        self,
        *,
        connection_repository: GarminConnectionRepository,
        health_repository,
        client: GarminSyncClient,
    ):
        self.connection_repository = connection_repository
        self.health_repository = health_repository
        self.client = client

    def sync_recent(
        self,
        user_id: UUID,
        *,
        days: int,
        today: date | None = None,
        now: datetime | None = None,
        activity_limit_per_day: int = 50,
        activity_details_limit: int = 5,
        daily_health_chunk_days: int = 90,
    ) -> GarminSyncSummary:
        if days < 1:
            raise ValueError("days must be at least 1")
        today = today or date.today()
        now = now or datetime.now(timezone.utc)
        start_date = today - timedelta(days=days - 1)
        session_dump = self.connection_repository.get_session_dump(user_id)
        if not session_dump:
            raise RuntimeError("Garmin is not connected for this user.")

        try:
            daily_count = self._sync_daily_health(
                user_id, session_dump,
                start_date=start_date, end_date=today, now=now,
                chunk_days=daily_health_chunk_days,
            )
            activity_count = self._sync_activities(
                user_id, session_dump,
                start_date=start_date, end_date=today, now=now,
                limit=activity_limit_per_day,
            )
            detail_count = self._sync_activity_details(
                user_id, session_dump, now=now, limit=activity_details_limit,
            )
        except Exception as error:
            self.connection_repository.mark_sync_failure(user_id, _safe_error(error))
            raise

        self.connection_repository.mark_sync_success(user_id, now)
        return GarminSyncSummary(
            daily_health_records=daily_count,
            activity_records=activity_count,
            activity_detail_records=detail_count,
            start_date=start_date,
            end_date=today,
        )

    def sync_if_stale(
        self,
        user_id: UUID,
        *,
        stale_after_minutes: int = 10,
        days: int = 2,
    ) -> bool:
        last = self.connection_repository.get_last_sync_at(user_id)
        now = datetime.now(timezone.utc)
        if last is not None and (now - last).total_seconds() < stale_after_minutes * 60:
            return False
        self.sync_recent(user_id, days=days, now=now)
        return True

    def _sync_daily_health(
        self, user_id: UUID, session_dump: str, *,
        start_date: date, end_date: date, now: datetime, chunk_days: int,
    ) -> int:
        from trellis.infra_tracking import (
            GarminDailyHealthRecord, GarminHealthProvenance, HealthSyncKind, HealthSyncRun,
        )
        total = 0
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end_date)
            run = self.health_repository.start_sync(
                HealthSyncRun(
                    user_id=user_id, kind=HealthSyncKind.DAILY_HEALTH,
                    start_date=chunk_start, end_date=chunk_end, started_at=now,
                )
            )
            try:
                metrics = self.client.sync(session_dump, chunk_start, chunk_end)
                for metric in metrics:
                    self.health_repository.upsert_daily_health(
                        GarminDailyHealthRecord.from_garmin(
                            user_id, metric,
                            provenance=GarminHealthProvenance(
                                sync_run_id=run.id, fetched_at=now, worker_endpoint="/sync",
                            ),
                        )
                    )
            except Exception as error:
                self.health_repository.finish_sync(
                    run.failed(completed_at=now, error=_safe_error(error))
                )
                raise
            self.health_repository.finish_sync(
                run.succeeded(completed_at=now, records_upserted=len(metrics))
            )
            total += len(metrics)
            chunk_start = chunk_end + timedelta(days=1)
        return total

    def _sync_activities(
        self, user_id: UUID, session_dump: str, *,
        start_date: date, end_date: date, now: datetime, limit: int,
    ) -> int:
        from trellis.infra_tracking import (
            GarminActivityRecord, GarminHealthProvenance, HealthSyncKind, HealthSyncRun,
        )
        run = self.health_repository.start_sync(
            HealthSyncRun(
                user_id=user_id, kind=HealthSyncKind.ACTIVITIES,
                start_date=start_date, end_date=end_date, started_at=now,
            )
        )
        seen: set[str] = set()
        try:
            day = start_date
            while day <= end_date:
                for activity in self.client.activities(session_dump, limit=limit, on_date=day):
                    self.health_repository.upsert_activity(
                        GarminActivityRecord.from_garmin(
                            user_id, activity,
                            provenance=GarminHealthProvenance(
                                sync_run_id=run.id, fetched_at=now,
                                worker_endpoint="/activities",
                            ),
                        )
                    )
                    seen.add(activity.activity_id)
                day += timedelta(days=1)
        except Exception as error:
            self.health_repository.finish_sync(
                run.failed(completed_at=now, error=_safe_error(error))
            )
            raise
        self.health_repository.finish_sync(
            run.succeeded(completed_at=now, records_upserted=len(seen))
        )
        return len(seen)

    def _sync_activity_details(
        self, user_id: UUID, session_dump: str, *, now: datetime, limit: int,
    ) -> int:
        from trellis.infra_tracking import HealthSyncKind, HealthSyncRun
        if limit == 0 or not hasattr(self.health_repository, "latest_activities"):
            return 0
        run = self.health_repository.start_sync(
            HealthSyncRun(
                user_id=user_id, kind=HealthSyncKind.ACTIVITY_DETAILS,
                started_at=now, metadata={"limit": limit},
            )
        )
        count = 0
        try:
            activities = self.health_repository.latest_activities(
                user_id, limit=limit, activity_type=None,
            )
            for activity in activities:
                detail = self.client.activity_detail(session_dump, activity.activity_id)
                self.health_repository.upsert_activity_detail(
                    user_id=user_id,
                    activity_id=activity.activity_id,
                    raw_data=dict(detail.raw),
                    sync_run_id=run.id,
                )
                count += 1
        except Exception as error:
            self.health_repository.finish_sync(
                run.failed(completed_at=now, error=_safe_error(error))
            )
            raise
        self.health_repository.finish_sync(
            run.succeeded(completed_at=now, records_upserted=count)
        )
        return count


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def _select_telegram_user(settings) -> int:
    allowed = sorted(settings.telegram_allowed_users)
    if len(allowed) == 1:
        return allowed[0]
    if allowed:
        print("Known Telegram user IDs:")
        for value in allowed:
            print(f"- {value}")
    raw = input("Telegram user ID to attach Garmin to: ").strip()
    try:
        return int(raw)
    except ValueError as error:
        raise SystemExit("Telegram user ID must be an integer.") from error


def main_setup() -> None:
    from trellis.core_config import Settings
    from trellis.infra_postgres import PostgresDatabase

    settings = Settings.from_env()
    settings.validate_health()

    database = PostgresDatabase(settings.database_url)
    database.migrate(Path(__file__).with_name("migrations"))

    telegram_user_id = _select_telegram_user(settings)
    user_id = database.ensure_user(telegram_user_id, str(settings.timezone))

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    if not email or not password:
        raise SystemExit("Garmin email and password are required.")

    client = GarminClient(
        settings.health_worker_url, settings.health_worker_secret, timeout=90.0,
    )
    result = client.connect(email, password)
    password = ""

    if result.status is GarminAuthStatus.MFA_REQUIRED:
        code = input("Garmin MFA code: ").strip()
        result = client.complete_mfa(result.mfa_session_id or "", code)

    if result.status is not GarminAuthStatus.SUCCESS or not result.session_dump:
        raise SystemExit("Garmin did not return a usable session.")

    PostgresGarminConnectionRepository(database, settings.trellis_secret_key).save_connected(
        user_id, email=email, session_dump=result.session_dump,
    )
    print("Garmin connected. Session stored encrypted in PostgreSQL.")


def main_sync() -> None:
    from trellis.core_config import Settings
    from trellis.infra_postgres import PostgresDatabase
    from trellis.infra_tracking import PostgresHealthRepository

    parser = argparse.ArgumentParser(description="Sync Garmin health data into Trellis.")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--activity-details-limit", type=int, default=5)
    parser.add_argument("--daily-health-chunk-days", type=int, default=90)
    args = parser.parse_args()

    settings = Settings.from_env()
    settings.validate_health()
    database = PostgresDatabase(settings.database_url)
    database.migrate(Path(__file__).with_name("migrations"))

    telegram_user_id = _select_telegram_user(settings)
    user_id = database.ensure_user(telegram_user_id, str(settings.timezone))

    connection_repository = PostgresGarminConnectionRepository(
        database, settings.trellis_secret_key,
    )
    service = GarminSyncService(
        connection_repository=connection_repository,
        health_repository=PostgresHealthRepository(database),
        client=GarminClient(
            settings.health_worker_url, settings.health_worker_secret, timeout=120.0,
        ),
    )

    try:
        summary = service.sync_recent(
            user_id,
            days=args.days,
            activity_details_limit=args.activity_details_limit,
            daily_health_chunk_days=args.daily_health_chunk_days,
        )
    except Exception as error:
        raise SystemExit(f"Garmin sync failed: {_safe_error(error)}") from error

    print(
        f"Garmin sync complete: {summary.daily_health_records} daily health records, "
        f"{summary.activity_records} activities, {summary.activity_detail_records} activity details "
        f"({summary.start_date.isoformat()} to {summary.end_date.isoformat()})."
    )


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_auth(value: Any, *, endpoint: str) -> GarminAuthResult:
    payload = _mapping(value, f"{endpoint} response")
    status = payload.get("status")
    if status == GarminAuthStatus.SUCCESS.value:
        session_dump = _required_string(payload.get("session_dump"), f"{endpoint}.session_dump")
        return GarminAuthResult(status=GarminAuthStatus.SUCCESS, session_dump=session_dump)
    if status == GarminAuthStatus.MFA_REQUIRED.value:
        session_id = _required_string(payload.get("session_id"), f"{endpoint}.session_id")
        return GarminAuthResult(status=GarminAuthStatus.MFA_REQUIRED, mfa_session_id=session_id)
    raise GarminResponseError(f"{endpoint}.status has unsupported value {status!r}")


def _normalize_health(value: Any, *, fallback_date: date | None, location: str) -> GarminDailyHealth:
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
        sleep_duration_minutes=_integer(payload.get("sleep_duration_minutes"), f"{location}.sleep_duration_minutes"),
        sleep_deep_minutes=_integer(payload.get("sleep_deep_minutes"), f"{location}.sleep_deep_minutes"),
        sleep_light_minutes=_integer(payload.get("sleep_light_minutes"), f"{location}.sleep_light_minutes"),
        sleep_rem_minutes=_integer(payload.get("sleep_rem_minutes"), f"{location}.sleep_rem_minutes"),
        sleep_awake_minutes=_integer(payload.get("sleep_awake_minutes"), f"{location}.sleep_awake_minutes"),
        sleep_score=_integer(payload.get("sleep_score"), f"{location}.sleep_score"),
        body_battery_maximum=_integer(payload.get("body_battery_max"), f"{location}.body_battery_max"),
        body_battery_minimum=_integer(payload.get("body_battery_min"), f"{location}.body_battery_min"),
        body_battery_end=_integer(payload.get("body_battery_end"), f"{location}.body_battery_end"),
        body_battery_charged=_integer(payload.get("body_battery_charged"), f"{location}.body_battery_charged"),
        body_battery_drained=_integer(payload.get("body_battery_drained"), f"{location}.body_battery_drained"),
        average_stress=_integer(payload.get("stress_avg"), f"{location}.stress_avg"),
        maximum_stress=_integer(payload.get("stress_max"), f"{location}.stress_max"),
        stress_rest_minutes=_integer(payload.get("stress_rest_duration_minutes"), f"{location}.stress_rest_duration_minutes"),
        hrv_weekly_average=_number(payload.get("hrv_weekly_avg"), f"{location}.hrv_weekly_avg"),
        hrv_last_night=_number(payload.get("hrv_last_night"), f"{location}.hrv_last_night"),
        hrv_status=_optional_string(payload.get("hrv_status"), f"{location}.hrv_status"),
        raw=payload,
    )


def _normalize_activity(value: Any, *, location: str) -> GarminActivity:
    payload = dict(_mapping(value, location))
    return GarminActivity(
        activity_id=_identifier(payload.get("activityId"), f"{location}.activityId"),
        name=_required_string(payload.get("activityName"), f"{location}.activityName"),
        activity_type=_required_string(payload.get("activityType"), f"{location}.activityType"),
        start_time_epoch_seconds=_integer(payload.get("startTimeInSeconds"), f"{location}.startTimeInSeconds"),
        duration_milliseconds=_duration_milliseconds(payload.get("duration"), f"{location}.duration"),
        calories=_integer(payload.get("calories"), f"{location}.calories"),
        average_heart_rate=_integer(payload.get("avgHeartRate"), f"{location}.avgHeartRate"),
        maximum_heart_rate=_integer(payload.get("maxHeartRate"), f"{location}.maxHeartRate"),
        distance_meters=_number(payload.get("distance"), f"{location}.distance"),
        elevation_gain_meters=_number(payload.get("elevationGain"), f"{location}.elevationGain"),
        elevation_loss_meters=_number(payload.get("elevationLoss"), f"{location}.elevationLoss"),
        active_sets=_integer(payload.get("activeSets"), f"{location}.activeSets"),
        total_exercise_repetitions=_integer(payload.get("totalExerciseReps"), f"{location}.totalExerciseReps"),
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


def _duration_milliseconds(value: Any, location: str) -> float | None:
    duration = _number(value, location)
    if duration is None:
        return None
    if duration <= 24 * 60 * 60:
        return duration * 1000
    return duration


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


def _safe_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:2000]
