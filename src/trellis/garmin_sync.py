from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from trellis.config import Settings
from trellis.garmin import GarminActivity, GarminClient, GarminDailyHealth
from trellis.garmin_setup import (
    PostgresGarminConnectionRepository,
    _select_telegram_user,
)
from trellis.health import (
    GarminActivityRecord,
    GarminDailyHealthRecord,
    GarminHealthProvenance,
    HealthRepository,
    HealthSyncKind,
    HealthSyncRun,
)
from trellis.health_postgres import PostgresHealthRepository
from trellis.postgres import PostgresDatabase


class GarminConnectionRepository(Protocol):
    def get_session_dump(self, user_id: UUID) -> str | None:
        ...

    def get_last_sync_at(self, user_id: UUID) -> datetime | None:
        ...

    def mark_sync_success(self, user_id: UUID, synced_at: datetime) -> None:
        ...

    def mark_sync_failure(self, user_id: UUID, error: str) -> None:
        ...


class GarminSyncClient(Protocol):
    def sync(
        self,
        session_dump: str,
        start_date: date,
        end_date: date,
    ) -> tuple[GarminDailyHealth, ...]:
        ...

    def activities(
        self,
        session_dump: str,
        *,
        limit: int = 10,
        on_date: date | None = None,
    ) -> tuple[GarminActivity, ...]:
        ...

    def activity_detail(self, session_dump: str, activity_id: str):
        ...


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
        health_repository: HealthRepository,
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
        if activity_limit_per_day < 1:
            raise ValueError("activity_limit_per_day must be at least 1")
        if activity_details_limit < 0:
            raise ValueError("activity_details_limit cannot be negative")
        if daily_health_chunk_days < 1:
            raise ValueError("daily_health_chunk_days must be at least 1")

        today = today or date.today()
        now = now or datetime.now(timezone.utc)
        start_date = today - timedelta(days=days - 1)
        session_dump = self.connection_repository.get_session_dump(user_id)
        if not session_dump:
            raise RuntimeError("Garmin is not connected for this user.")

        try:
            daily_count = self._sync_daily_health(
                user_id,
                session_dump,
                start_date=start_date,
                end_date=today,
                now=now,
                chunk_days=daily_health_chunk_days,
            )
            activity_count = self._sync_activities(
                user_id,
                session_dump,
                start_date=start_date,
                end_date=today,
                now=now,
                limit=activity_limit_per_day,
            )
            detail_count = self._sync_activity_details(
                user_id,
                session_dump,
                now=now,
                limit=activity_details_limit,
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
        """Sync only if last sync was more than stale_after_minutes ago. Returns True if synced."""
        last = self.connection_repository.get_last_sync_at(user_id)
        now = datetime.now(timezone.utc)
        if last is not None and (now - last).total_seconds() < stale_after_minutes * 60:
            return False
        self.sync_recent(user_id, days=days, now=now)
        return True

    def _sync_daily_health(
        self,
        user_id: UUID,
        session_dump: str,
        *,
        start_date: date,
        end_date: date,
        now: datetime,
        chunk_days: int,
    ) -> int:
        total = 0
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end_date)
            total += self._sync_daily_health_chunk(
                user_id,
                session_dump,
                start_date=chunk_start,
                end_date=chunk_end,
                now=now,
            )
            chunk_start = chunk_end + timedelta(days=1)
        return total

    def _sync_daily_health_chunk(
        self,
        user_id: UUID,
        session_dump: str,
        *,
        start_date: date,
        end_date: date,
        now: datetime,
    ) -> int:
        run = self.health_repository.start_sync(
            HealthSyncRun(
                user_id=user_id,
                kind=HealthSyncKind.DAILY_HEALTH,
                start_date=start_date,
                end_date=end_date,
                started_at=now,
            )
        )
        try:
            metrics = self.client.sync(session_dump, start_date, end_date)
            for metric in metrics:
                self.health_repository.upsert_daily_health(
                    GarminDailyHealthRecord.from_garmin(
                        user_id,
                        metric,
                        provenance=GarminHealthProvenance(
                            sync_run_id=run.id,
                            fetched_at=now,
                            worker_endpoint="/sync",
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
        return len(metrics)

    def _sync_activities(
        self,
        user_id: UUID,
        session_dump: str,
        *,
        start_date: date,
        end_date: date,
        now: datetime,
        limit: int,
    ) -> int:
        run = self.health_repository.start_sync(
            HealthSyncRun(
                user_id=user_id,
                kind=HealthSyncKind.ACTIVITIES,
                start_date=start_date,
                end_date=end_date,
                started_at=now,
            )
        )
        seen: set[str] = set()
        try:
            day = start_date
            while day <= end_date:
                for activity in self.client.activities(
                    session_dump,
                    limit=limit,
                    on_date=day,
                ):
                    self.health_repository.upsert_activity(
                        GarminActivityRecord.from_garmin(
                            user_id,
                            activity,
                            provenance=GarminHealthProvenance(
                                sync_run_id=run.id,
                                fetched_at=now,
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
        self,
        user_id: UUID,
        session_dump: str,
        *,
        now: datetime,
        limit: int,
    ) -> int:
        if limit == 0 or not hasattr(self.health_repository, "latest_activities"):
            return 0
        run = self.health_repository.start_sync(
            HealthSyncRun(
                user_id=user_id,
                kind=HealthSyncKind.ACTIVITY_DETAILS,
                started_at=now,
                metadata={"limit": limit},
            )
        )
        count = 0
        try:
            activities = self.health_repository.latest_activities(
                user_id,
                limit=limit,
                activity_type=None,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Garmin health data into Trellis.")
    parser.add_argument("--days", type=int, default=2, help="Inclusive days to sync.")
    parser.add_argument(
        "--activity-details-limit",
        type=int,
        default=5,
        help="Number of latest activities to enrich with detail and splits.",
    )
    parser.add_argument(
        "--daily-health-chunk-days",
        type=int,
        default=90,
        help="Maximum date span per Garmin daily-health worker request.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    settings.validate_health()
    database = PostgresDatabase(settings.database_url)
    database.migrate(Path(__file__).with_name("migrations"))
    telegram_user_id = _select_telegram_user(settings)
    user_id = database.ensure_user(telegram_user_id, str(settings.timezone))

    connection_repository = PostgresGarminConnectionRepository(
        database,
        settings.trellis_secret_key,
    )
    service = GarminSyncService(
        connection_repository=connection_repository,
        health_repository=PostgresHealthRepository(database),
        client=GarminClient(
            settings.health_worker_url,
            settings.health_worker_secret,
            timeout=120.0,
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
        "Garmin sync complete: "
        f"{summary.daily_health_records} daily health records, "
        f"{summary.activity_records} activities "
        f"{summary.activity_detail_records} activity details "
        f"({summary.start_date.isoformat()} to {summary.end_date.isoformat()})."
    )


def _safe_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:2000]


if __name__ == "__main__":
    main()
