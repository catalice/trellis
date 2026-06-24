from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from uuid import UUID, uuid4

from trellis.health import GarminActivityRecord
from trellis.training_history import TrainingHistoryService


class FakeHistoryRepository:
    def __init__(self, activities: tuple[GarminActivityRecord, ...]):
        self.activities = activities

    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        activities = self.activities[:limit]
        if activity_type is None:
            return activities
        return tuple(activity for activity in activities if activity.activity_type == activity_type)


class TrainingHistoryServiceTest(unittest.TestCase):
    def test_summarizes_recent_running_history_for_planning(self):
        user_id = uuid4()
        service = TrainingHistoryService(
            FakeHistoryRepository(
                (
                    _run(user_id, "long", date(2026, 6, 1), 72, 11.2),
                    _run(user_id, "easy-1", date(2026, 5, 30), 38, 5.4),
                    _run(user_id, "easy-2", date(2026, 5, 28), 35, 5.1),
                    _run(user_id, "easy-3", date(2026, 5, 25), 32, 4.8),
                    _run(user_id, "easy-4", date(2026, 5, 22), 30, 4.3),
                    _run(user_id, "old", date(2025, 12, 1), 100, 16.0),
                )
            )
        )

        summary = service.summarize(user_id, as_of=date(2026, 6, 7))

        self.assertEqual(5, summary.runs_28d)
        self.assertEqual(30.8, summary.distance_28d_km)
        self.assertEqual(72, summary.longest_run_84d_minutes)
        self.assertEqual(75, summary.longest_run_anchor_minutes)
        self.assertIn("5 runs", summary.rationale[0])


def _run(
    user_id: UUID,
    activity_id: str,
    on_date: date,
    minutes: int,
    km: float,
) -> GarminActivityRecord:
    start = datetime(on_date.year, on_date.month, on_date.day, 7, tzinfo=timezone.utc)
    return GarminActivityRecord(
        user_id=user_id,
        activity_id=activity_id,
        name="Run",
        activity_type="running",
        start_time_epoch_seconds=int(start.timestamp()),
        duration_milliseconds=minutes * 60_000,
        distance_meters=km * 1000,
    )


if __name__ == "__main__":
    unittest.main()
