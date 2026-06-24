from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from trellis.garmin.models import GarminActivity, GarminDailyHealth
from trellis.garmin_sync import GarminSyncService
from trellis.health import HealthSyncStatus, InMemoryHealthRepository


class FakeConnectionRepository:
    def __init__(self, session_dump: str | None = "session"):
        self.session_dump = session_dump
        self.successes: list[tuple[UUID, datetime]] = []
        self.failures: list[tuple[UUID, str]] = []

    def get_session_dump(self, user_id: UUID) -> str | None:
        return self.session_dump

    def mark_sync_success(self, user_id: UUID, synced_at: datetime) -> None:
        self.successes.append((user_id, synced_at))

    def mark_sync_failure(self, user_id: UUID, error: str) -> None:
        self.failures.append((user_id, error))


class FakeGarminClient:
    def __init__(self):
        self.sync_calls = []
        self.activity_calls = []

    def sync(self, session_dump: str, start_date: date, end_date: date):
        self.sync_calls.append((session_dump, start_date, end_date))
        return (
            GarminDailyHealth(date=start_date, steps=1000, raw={"day": "start"}),
            GarminDailyHealth(date=end_date, steps=2000, raw={"day": "end"}),
        )

    def activities(
        self,
        session_dump: str,
        *,
        limit: int = 10,
        on_date: date | None = None,
    ):
        self.activity_calls.append((session_dump, limit, on_date))
        if on_date == date(2026, 6, 7):
            return (
                GarminActivity(
                    activity_id="run-1",
                    name="Morning Run",
                    activity_type="running",
                    raw={"date": "2026-06-07"},
                ),
            )
        return ()


class FailingGarminClient(FakeGarminClient):
    def sync(self, session_dump: str, start_date: date, end_date: date):
        raise RuntimeError("worker unavailable")


class GarminSyncServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = uuid4()
        self.now = datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc)
        self.health_repository = InMemoryHealthRepository()
        self.connection_repository = FakeConnectionRepository()
        self.client = FakeGarminClient()

    def service(self, client=None) -> GarminSyncService:
        return GarminSyncService(
            connection_repository=self.connection_repository,
            health_repository=self.health_repository,
            client=client or self.client,
        )

    def test_sync_recent_persists_daily_health_and_activities(self):
        summary = self.service().sync_recent(
            self.user_id,
            days=2,
            today=date(2026, 6, 7),
            now=self.now,
        )

        self.assertEqual(2, summary.daily_health_records)
        self.assertEqual(1, summary.activity_records)
        self.assertEqual(date(2026, 6, 6), summary.start_date)
        self.assertEqual(date(2026, 6, 7), summary.end_date)
        self.assertEqual(1000, self.health_repository.get_daily_health(self.user_id, date(2026, 6, 6)).steps)
        self.assertEqual("Morning Run", self.health_repository.get_activity(self.user_id, "run-1").name)
        self.assertEqual(
            [("session", date(2026, 6, 6), date(2026, 6, 7))],
            self.client.sync_calls,
        )
        self.assertEqual(
            [
                ("session", 50, date(2026, 6, 6)),
                ("session", 50, date(2026, 6, 7)),
            ],
            self.client.activity_calls,
        )
        self.assertEqual([(self.user_id, self.now)], self.connection_repository.successes)
        self.assertEqual([], self.connection_repository.failures)

    def test_sync_recent_chunks_large_daily_health_ranges(self):
        summary = self.service().sync_recent(
            self.user_id,
            days=181,
            today=date(2026, 6, 7),
            now=self.now,
            daily_health_chunk_days=90,
            activity_details_limit=0,
        )

        self.assertEqual(date(2025, 12, 9), summary.start_date)
        self.assertEqual(date(2026, 6, 7), summary.end_date)
        self.assertEqual(
            [
                ("session", date(2025, 12, 9), date(2026, 3, 8)),
                ("session", date(2026, 3, 9), date(2026, 6, 6)),
                ("session", date(2026, 6, 7), date(2026, 6, 7)),
            ],
            self.client.sync_calls,
        )

    def test_missing_connection_stops_before_worker_calls(self):
        self.connection_repository.session_dump = None

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            self.service().sync_recent(
                self.user_id,
                days=1,
                today=date(2026, 6, 7),
                now=self.now,
            )

        self.assertEqual([], self.client.sync_calls)

    def test_failure_marks_sync_run_and_connection_failure(self):
        with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
            self.service(FailingGarminClient()).sync_recent(
                self.user_id,
                days=1,
                today=date(2026, 6, 7),
                now=self.now,
            )

        self.assertEqual(1, len(self.connection_repository.failures))
        self.assertIn("worker unavailable", self.connection_repository.failures[0][1])
        runs = [
            run
            for run in self.health_repository._sync_runs.values()
            if run.status is HealthSyncStatus.FAILED
        ]
        self.assertEqual(1, len(runs))
        self.assertEqual("worker unavailable", runs[0].error)


if __name__ == "__main__":
    unittest.main()
