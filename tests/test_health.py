from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from uuid import uuid4

from trellis.garmin.models import GarminActivity, GarminDailyHealth
from trellis.health import (
    GarminActivityRecord,
    GarminDailyHealthRecord,
    GarminHealthProvenance,
    HealthSyncKind,
    HealthSyncRun,
    HealthSyncStatus,
    InMemoryHealthRepository,
    SelfHealthReport,
)


class HealthPersistenceFoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = uuid4()
        self.repository = InMemoryHealthRepository()
        self.sync_run_id = uuid4()
        self.fetched_at = datetime(2026, 6, 7, 7, 30, tzinfo=timezone.utc)

    def provenance(self) -> GarminHealthProvenance:
        return GarminHealthProvenance(
            sync_run_id=self.sync_run_id,
            fetched_at=self.fetched_at,
            worker_endpoint="/sync",
        )

    def test_normalizes_garmin_daily_health_and_preserves_raw(self):
        garmin = GarminDailyHealth(
            date=date(2026, 6, 7),
            steps=10234,
            sleep_duration_minutes=445,
            resting_heart_rate=52,
            body_battery_end=41,
            hrv_last_night=48.5,
            raw={"future_metric": "kept"},
        )

        record = GarminDailyHealthRecord.from_garmin(
            self.user_id,
            garmin,
            provenance=self.provenance(),
        )

        self.assertEqual(self.user_id, record.user_id)
        self.assertEqual(date(2026, 6, 7), record.observed_on)
        self.assertEqual(10234, record.steps)
        self.assertEqual(52, record.resting_heart_rate)
        self.assertEqual(41, record.body_battery_end)
        self.assertEqual(48.5, record.hrv_last_night)
        self.assertEqual("kept", record.raw["future_metric"])
        self.assertEqual(self.sync_run_id, record.provenance.sync_run_id)

    def test_invalid_garmin_score_values_are_stored_as_missing(self):
        record = GarminDailyHealthRecord.from_garmin(
            self.user_id,
            GarminDailyHealth(
                date=date(2025, 6, 29),
                average_stress=-2,
                body_battery_end=103,
                sleep_score=-1,
                raw={"average_stress": -2},
            ),
        )

        self.assertIsNone(record.average_stress)
        self.assertIsNone(record.body_battery_end)
        self.assertIsNone(record.sleep_score)
        self.assertEqual(-2, record.raw["average_stress"])

    def test_daily_health_upsert_is_idempotent_by_user_and_date(self):
        first = GarminDailyHealthRecord.from_garmin(
            self.user_id,
            GarminDailyHealth(
                date=date(2026, 6, 7),
                steps=1000,
                raw={"version": 1},
            ),
        )
        second = GarminDailyHealthRecord.from_garmin(
            self.user_id,
            GarminDailyHealth(
                date=date(2026, 6, 7),
                steps=1200,
                sleep_score=82,
                raw={"version": 2},
            ),
        )

        self.repository.upsert_daily_health(first)
        stored = self.repository.upsert_daily_health(second)

        self.assertEqual(stored, self.repository.get_daily_health(self.user_id, date(2026, 6, 7)))
        self.assertEqual(1200, stored.steps)
        self.assertEqual(82, stored.sleep_score)
        self.assertEqual({"version": 2}, stored.raw)

    def test_activities_upsert_by_garmin_activity_id(self):
        original = GarminActivityRecord.from_garmin(
            self.user_id,
            GarminActivity(
                activity_id="987654",
                name="Barcelona Run",
                activity_type="running",
                distance_meters=10012.5,
                raw={"version": 1},
            ),
        )
        updated = GarminActivityRecord.from_garmin(
            self.user_id,
            GarminActivity(
                activity_id="987654",
                name="Barcelona Run",
                activity_type="running",
                distance_meters=10100,
                average_heart_rate=151,
                raw={"version": 2},
            ),
        )

        self.repository.upsert_activity(original)
        stored = self.repository.upsert_activity(updated)

        self.assertEqual(
            stored,
            self.repository.get_activity(self.user_id, "987654"),
        )
        self.assertEqual(10100, stored.distance_meters)
        self.assertEqual(151, stored.average_heart_rate)
        self.assertEqual({"version": 2}, stored.raw)

    def test_self_reports_are_preserved_separately_from_garmin(self):
        self.repository.upsert_daily_health(
            GarminDailyHealthRecord.from_garmin(
                self.user_id,
                GarminDailyHealth(
                    date=date(2026, 6, 7),
                    sleep_duration_minutes=360,
                ),
            )
        )
        report = SelfHealthReport(
            user_id=self.user_id,
            observed_on=date(2026, 6, 7),
            energy_score=8,
            life_load_score=6,
            sleep_minutes=420,
            note="I think I slept about 7h.",
            raw={"message": "slept 7h, body 8/10"},
        )

        stored = self.repository.record_self_report(report)

        garmin = self.repository.get_daily_health(self.user_id, date(2026, 6, 7))
        self.assertIsNotNone(garmin)
        assert garmin is not None
        self.assertEqual(360, garmin.sleep_duration_minutes)
        self.assertEqual((stored,), self.repository.list_self_reports(self.user_id, date(2026, 6, 7)))
        self.assertEqual(420, stored.sleep_minutes)

    def test_multiple_self_reports_for_same_day_are_not_collapsed(self):
        morning = SelfHealthReport(
            user_id=self.user_id,
            observed_on=date(2026, 6, 7),
            energy_score=6,
            reported_at=datetime(2026, 6, 7, 8, tzinfo=timezone.utc),
        )
        evening = SelfHealthReport(
            user_id=self.user_id,
            observed_on=date(2026, 6, 7),
            energy_score=3,
            reported_at=datetime(2026, 6, 7, 21, tzinfo=timezone.utc),
        )

        self.repository.record_self_report(evening)
        self.repository.record_self_report(morning)

        self.assertEqual(
            (morning, evening),
            self.repository.list_self_reports(self.user_id, date(2026, 6, 7)),
        )

    def test_sync_status_tracks_success_metadata(self):
        run = HealthSyncRun(
            user_id=self.user_id,
            kind=HealthSyncKind.DAILY_HEALTH,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 7),
            started_at=datetime(2026, 6, 7, 7, tzinfo=timezone.utc),
        )

        self.repository.start_sync(run)
        completed = run.succeeded(
            completed_at=datetime(2026, 6, 7, 7, 1, tzinfo=timezone.utc),
            records_upserted=7,
            metadata={"source": "daily cron"},
        )
        self.repository.finish_sync(completed)

        stored = self.repository.get_sync(run.id)
        self.assertEqual(HealthSyncStatus.SUCCEEDED, stored.status)
        self.assertEqual(7, stored.records_upserted)
        self.assertEqual({"source": "daily cron"}, stored.metadata)

    def test_sync_status_tracks_sanitized_failure(self):
        run = HealthSyncRun(
            user_id=self.user_id,
            kind=HealthSyncKind.ACTIVITIES,
            started_at=datetime(2026, 6, 7, 7, tzinfo=timezone.utc),
        )

        self.repository.start_sync(run)
        failed = run.failed(
            completed_at=datetime(2026, 6, 7, 7, 1, tzinfo=timezone.utc),
            error="Garmin worker unavailable",
        )
        self.repository.finish_sync(failed)

        stored = self.repository.get_sync(run.id)
        self.assertEqual(HealthSyncStatus.FAILED, stored.status)
        self.assertEqual("Garmin worker unavailable", stored.error)

    def test_self_report_validates_score_ranges(self):
        with self.assertRaisesRegex(ValueError, "energy_score"):
            SelfHealthReport(
                user_id=self.user_id,
                observed_on=date(2026, 6, 7),
                energy_score=11,
            )
        with self.assertRaisesRegex(ValueError, "sleep_minutes"):
            SelfHealthReport(
                user_id=self.user_id,
                observed_on=date(2026, 6, 7),
                sleep_minutes=-1,
            )


if __name__ == "__main__":
    unittest.main()
