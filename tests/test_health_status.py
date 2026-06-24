from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from uuid import uuid4

from trellis.garmin_setup import GarminConnectionStatus
from trellis.health import GarminActivityRecord, GarminDailyHealthRecord
from trellis.health_status import HealthStatusService


class FakeHealthRepository:
    def __init__(self, latest=None, activity=None, detail=None):
        self.latest = latest
        self.activity = activity
        self.detail = detail

    def latest_daily_health(self, user_id):
        return self.latest

    def latest_activity(self, user_id):
        return self.activity

    def latest_activity_detail(self, user_id, *, activity_type=None):
        return self.detail


class FakeConnectionRepository:
    def __init__(self, status):
        self._status = status

    def status(self, user_id):
        return self._status


class HealthStatusServiceTest(unittest.TestCase):
    def test_reports_latest_garmin_metrics_without_raw_payload(self):
        user_id = uuid4()
        service = HealthStatusService(
            FakeHealthRepository(
                GarminDailyHealthRecord(
                    user_id=user_id,
                    observed_on=date(2026, 6, 7),
                    steps=8500,
                    sleep_duration_minutes=442,
                    sleep_score=81,
                    body_battery_end=62,
                    resting_heart_rate=54,
                    hrv_last_night=48.5,
                    average_stress=31,
                    raw={"private": "preserved but not shown"},
                )
            ),
            FakeConnectionRepository(
                GarminConnectionStatus(
                    is_connected=True,
                    sync_enabled=True,
                    last_sync_at=datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc),
                )
            ),
        )

        response = service.telegram_summary(user_id, "what does garmin say?")

        self.assertIn("Latest Garmin data: 2026-06-07", response)
        self.assertIn("Sleep: 7h 22m", response)
        self.assertIn("Body battery: 62", response)
        self.assertIn("Resting HR: 54 bpm", response)
        self.assertIn("HRV: 48.5 ms", response)
        self.assertNotIn("private", response)

    def test_reports_latest_activity_for_activity_query(self):
        user_id = uuid4()
        response = HealthStatusService(
            FakeHealthRepository(
                activity=GarminActivityRecord(
                    user_id=user_id,
                    activity_id="123",
                    name="Barcelona Run",
                    activity_type="running",
                    distance_meters=5020,
                    duration_milliseconds=1_830_000,
                    average_heart_rate=151,
                    maximum_heart_rate=172,
                    calories=410,
                )
            ),
            FakeConnectionRepository(
                GarminConnectionStatus(
                    is_connected=True,
                    sync_enabled=True,
                    last_sync_at=datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc),
                )
            ),
        ).telegram_summary(user_id, "most recent activity?")

        self.assertIn("Most recent Garmin activity", response)
        self.assertIn("Name: Barcelona Run", response)
        self.assertIn("Distance: 5.02 km", response)
        self.assertIn("Duration: 30m 30s", response)
        self.assertIn("Average HR: 151 bpm", response)

    def test_reports_no_stored_activities_for_activity_query(self):
        response = HealthStatusService(
            FakeHealthRepository(None, activity=None),
            FakeConnectionRepository(
                GarminConnectionStatus(
                    is_connected=True,
                    sync_enabled=True,
                    last_sync_at=datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc),
                )
            ),
        ).telegram_summary(uuid4(), "most recent activity?")

        self.assertIn("No Garmin activities are stored yet.", response)
        self.assertIn("Try a longer Garmin sync window", response)

    def test_reports_latest_run_splits(self):
        user_id = uuid4()
        response = HealthStatusService(
            FakeHealthRepository(
                activity=GarminActivityRecord(
                    user_id=user_id,
                    activity_id="123",
                    name="Barcelona Running",
                    activity_type="running",
                ),
                detail={
                    "splits": [
                        {"distance": 1000, "duration": 360, "averageHR": 145},
                        {"distance": 1000, "duration": 345, "averageHR": 151},
                    ]
                },
            ),
            FakeConnectionRepository(
                GarminConnectionStatus(is_connected=True, sync_enabled=True)
            ),
        ).telegram_summary(user_id, "show splits for latest run")

        self.assertIn("Latest run workout segments: Barcelona Running", response)
        self.assertIn("1. 1.00 km | 6m 00s | 6:00/km | 145 bpm", response)
        self.assertIn("2. 1.00 km | 5m 45s | 5:45/km | 151 bpm", response)

    def test_prefers_real_intervals_over_garmin_microsegments(self):
        user_id = uuid4()
        response = HealthStatusService(
            FakeHealthRepository(
                activity=GarminActivityRecord(
                    user_id=user_id,
                    activity_id="123",
                    name="Barcelona Running",
                    activity_type="running",
                ),
                detail={
                    "typed_splits": {
                        "splits": [
                            {
                                "type": "RWD_RUN",
                                "distance": 15,
                                "duration": 11,
                                "averageHR": 141,
                            },
                            {
                                "type": "INTERVAL_ACTIVE",
                                "distance": 4820,
                                "duration": 2284,
                                "averageHR": 148,
                            },
                            {
                                "type": "RWD_STAND",
                                "duration": 1,
                                "averageHR": 87,
                            },
                        ]
                    }
                },
            ),
            FakeConnectionRepository(
                GarminConnectionStatus(is_connected=True, sync_enabled=True)
            ),
        ).telegram_summary(user_id, "show splits for latest run")

        self.assertIn(
            "1. active interval | 4.82 km | 38m 04s | 7:54/km | 148 bpm",
            response,
        )
        self.assertNotIn("rwd stand", response)
        self.assertNotIn("0.00 km", response)

    def test_keeps_time_based_workout_intervals_without_distance(self):
        user_id = uuid4()
        response = HealthStatusService(
            FakeHealthRepository(
                activity=GarminActivityRecord(
                    user_id=user_id,
                    activity_id="123",
                    name="Track Intervals",
                    activity_type="running",
                ),
                detail={
                    "typed_splits": {
                        "splits": [
                            {
                                "type": "INTERVAL_ACTIVE",
                                "duration": 180,
                                "averageHR": 160,
                            },
                            {
                                "type": "INTERVAL_REST",
                                "duration": 90,
                                "averageHR": 136,
                            },
                        ]
                    }
                },
            ),
            FakeConnectionRepository(
                GarminConnectionStatus(is_connected=True, sync_enabled=True)
            ),
        ).telegram_summary(user_id, "show intervals for latest run")

        self.assertIn("Latest run workout segments: Track Intervals", response)
        self.assertIn("1. active interval | 3m 00s | 160 bpm", response)
        self.assertIn("2. rest interval | 1m 30s | 136 bpm", response)
        self.assertNotIn("km", response)

    def test_reports_connected_without_synced_data(self):
        response = HealthStatusService(
            FakeHealthRepository(None),
            FakeConnectionRepository(
                GarminConnectionStatus(is_connected=True, sync_enabled=True)
            ),
        ).telegram_summary(uuid4())

        self.assertEqual(
            "Garmin is connected, but no health data is stored yet. Run a sync first.",
            response,
        )

    def test_reports_not_connected(self):
        response = HealthStatusService(
            FakeHealthRepository(None),
            FakeConnectionRepository(
                GarminConnectionStatus(is_connected=False, sync_enabled=False)
            ),
        ).telegram_summary(uuid4())

        self.assertEqual("Garmin is not connected yet.", response)


if __name__ == "__main__":
    unittest.main()
