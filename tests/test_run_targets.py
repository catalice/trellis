from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from trellis.health import GarminActivityRecord
from trellis.run_targets import RunTargetCalibrationService


class FakeRunTargetRepository:
    def __init__(
        self,
        activities: tuple[GarminActivityRecord, ...],
        details: dict[str, dict] | None = None,
    ):
        self.activities = activities
        self.details = details or {}

    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        activities = self.activities[:limit]
        if activity_type:
            activities = tuple(
                activity
                for activity in activities
                if activity.activity_type == activity_type
            )
        return activities

    def activity_detail(self, user_id: UUID, activity_id: str) -> dict | None:
        return self.details.get(activity_id)


class RunTargetCalibrationServiceTest(unittest.TestCase):
    def test_reports_insufficient_data_without_recent_runs(self):
        result = RunTargetCalibrationService(FakeRunTargetRepository(())).calibrate(
            uuid4()
        )

        self.assertFalse(result.calibrated)
        self.assertFalse(result.easy_run.calibrated)
        self.assertIn("Need at least 3", result.easy_run.reasons[0])
        self.assertFalse(result.long_run.calibrated)
        self.assertFalse(result.interval.calibrated)

    def test_calibrates_easy_and_long_targets_from_steady_runs(self):
        user_id = uuid4()
        result = RunTargetCalibrationService(
            FakeRunTargetRepository(
                (
                    _run(user_id, "1", minutes=62, km=8.0, avg_hr=143, max_hr=165),
                    _run(user_id, "2", minutes=36, km=5.0, avg_hr=146, max_hr=166),
                    _run(user_id, "3", minutes=34, km=4.8, avg_hr=144, max_hr=162),
                    _run(user_id, "4", minutes=31, km=4.4, avg_hr=145, max_hr=160),
                )
            )
        ).calibrate(user_id)

        self.assertTrue(result.easy_run.calibrated)
        self.assertEqual(4, result.easy_run.sample_size)
        self.assertIsNotNone(result.easy_run.pace_range)
        self.assertIsNotNone(result.easy_run.heart_rate_range)
        self.assertEqual(138, result.easy_run.heart_rate_range.low_bpm)
        self.assertEqual(152, result.easy_run.heart_rate_range.high_bpm)
        self.assertTrue(result.long_run.calibrated)
        self.assertEqual(1, result.long_run.sample_size)
        self.assertGreater(
            result.long_run.pace_range.slow_seconds_per_km,
            result.easy_run.pace_range.slow_seconds_per_km,
        )

    def test_interval_target_uses_active_interval_segments(self):
        user_id = uuid4()
        result = RunTargetCalibrationService(
            FakeRunTargetRepository(
                (
                    _run(user_id, "intervals", minutes=45, km=7.0, avg_hr=151, max_hr=176),
                    _run(user_id, "easy-1", minutes=35, km=5.0, avg_hr=143, max_hr=160),
                    _run(user_id, "easy-2", minutes=37, km=5.1, avg_hr=144, max_hr=161),
                    _run(user_id, "easy-3", minutes=38, km=5.2, avg_hr=145, max_hr=162),
                ),
                details={
                    "intervals": {
                        "typed_splits": {
                            "splits": [
                                _segment("INTERVAL_ACTIVE", distance=650, duration=180, hr=162),
                                _segment("INTERVAL_REST", distance=180, duration=120, hr=135),
                                _segment("INTERVAL_ACTIVE", distance=640, duration=180, hr=164),
                                _segment("INTERVAL_REST", distance=170, duration=120, hr=136),
                                _segment("INTERVAL_ACTIVE", distance=630, duration=180, hr=165),
                            ]
                        }
                    }
                },
            )
        ).calibrate(user_id)

        self.assertTrue(result.interval.calibrated)
        self.assertEqual(3, result.interval.sample_size)
        self.assertIsNotNone(result.interval.pace_range)
        self.assertIsNotNone(result.interval.heart_rate_range)
        self.assertEqual(159, result.interval.heart_rate_range.low_bpm)
        self.assertEqual(169, result.interval.heart_rate_range.high_bpm)

    def test_interval_target_can_return_provisional_hr_without_pace(self):
        user_id = uuid4()
        result = RunTargetCalibrationService(
            FakeRunTargetRepository(
                (
                    _run(user_id, "1", minutes=31, km=4.4, avg_hr=145, max_hr=160),
                    _run(user_id, "2", minutes=35, km=5.0, avg_hr=146, max_hr=162),
                    _run(user_id, "3", minutes=36, km=5.1, avg_hr=147, max_hr=166),
                    _run(user_id, "4", minutes=38, km=5.4, avg_hr=148, max_hr=168),
                    _run(user_id, "5", minutes=40, km=5.5, avg_hr=149, max_hr=170),
                )
            )
        ).calibrate(user_id)

        self.assertFalse(result.interval.calibrated)
        self.assertEqual(0.35, result.interval.confidence)
        self.assertIsNone(result.interval.pace_range)
        self.assertEqual(148, result.interval.heart_rate_range.low_bpm)
        self.assertEqual(158, result.interval.heart_rate_range.high_bpm)
        self.assertIn("provisional HR range", result.interval.reasons[1])


def _run(
    user_id: UUID,
    activity_id: str,
    *,
    minutes: float,
    km: float,
    avg_hr: int,
    max_hr: int,
) -> GarminActivityRecord:
    return GarminActivityRecord(
        user_id=user_id,
        activity_id=activity_id,
        name="Run",
        activity_type="running",
        duration_milliseconds=minutes * 60_000,
        distance_meters=km * 1000,
        average_heart_rate=avg_hr,
        maximum_heart_rate=max_hr,
    )


def _segment(
    split_type: str,
    *,
    distance: float,
    duration: float,
    hr: float,
) -> dict:
    return {
        "type": split_type,
        "distance": distance,
        "duration": duration,
        "averageHR": hr,
    }


if __name__ == "__main__":
    unittest.main()
