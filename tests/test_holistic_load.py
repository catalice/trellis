from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from trellis.health import GarminActivityRecord
from trellis.holistic_load import HolisticLoadService, WeeklyLoadSignal
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _activity(
    user_id: UUID,
    activity_id: str,
    activity_type: str,
    on_date: date,
    minutes: int,
) -> GarminActivityRecord:
    start = datetime(on_date.year, on_date.month, on_date.day, 8, tzinfo=timezone.utc)
    return GarminActivityRecord(
        user_id=user_id,
        activity_id=activity_id,
        name=activity_type,
        activity_type=activity_type,
        start_time_epoch_seconds=int(start.timestamp()),
        duration_milliseconds=minutes * 60_000,
    )


class FakeActivitySource:
    def __init__(self, activities: tuple[GarminActivityRecord, ...]):
        self._activities = activities

    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        result = self._activities[:limit]
        if activity_type is not None:
            result = tuple(a for a in result if a.activity_type == activity_type)
        return result


# ---------------------------------------------------------------------------
# HolisticLoadService tests
# ---------------------------------------------------------------------------

class TestWeeklySignal(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = uuid4()
        self.as_of = date(2026, 6, 10)

    def test_counts_running_minutes_separately(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "r1", "running", date(2026, 6, 8), 45),
            _activity(self.user_id, "r2", "running", date(2026, 6, 6), 30),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(75, signal.running_minutes_7d)
        self.assertEqual(0, signal.other_activity_minutes_7d)
        self.assertEqual(2, signal.total_hard_sessions_7d)

    def test_counts_strength_as_hard_and_other(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "s1", "strength_training", date(2026, 6, 9), 50),
            _activity(self.user_id, "s2", "weight_training", date(2026, 6, 7), 40),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(0, signal.running_minutes_7d)
        self.assertEqual(90, signal.other_activity_minutes_7d)
        self.assertEqual(2, signal.total_hard_sessions_7d)

    def test_counts_boxing_as_hard_other(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "b1", "boxing", date(2026, 6, 8), 60),
            _activity(self.user_id, "b2", "muay_thai", date(2026, 6, 6), 60),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(120, signal.other_activity_minutes_7d)
        self.assertEqual(2, signal.total_hard_sessions_7d)

    def test_cycling_not_counted_as_hard(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "c1", "cycling", date(2026, 6, 8), 60),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(60, signal.other_activity_minutes_7d)
        self.assertEqual(0, signal.total_hard_sessions_7d)

    def test_unknown_short_activity_not_counted(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "x1", "yoga", date(2026, 6, 8), 25),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(0, signal.other_activity_minutes_7d)
        self.assertEqual(0, signal.total_hard_sessions_7d)

    def test_unknown_long_activity_counted_as_other(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "x2", "yoga", date(2026, 6, 8), 45),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(45, signal.other_activity_minutes_7d)
        self.assertEqual(0, signal.total_hard_sessions_7d)

    def test_excludes_activities_older_than_7_days(self):
        # as_of = 2026-06-10, window_start = as_of - 6 days = 2026-06-04
        repo = FakeActivitySource((
            _activity(self.user_id, "r1", "running", date(2026, 6, 4), 60),   # on window edge — included
            _activity(self.user_id, "r2", "running", date(2026, 6, 3), 60),   # outside (7 days before as_of)
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(60, signal.running_minutes_7d)
        self.assertEqual(1, signal.total_hard_sessions_7d)

    def test_mixed_week_totals(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "r1", "running", date(2026, 6, 9), 60),
            _activity(self.user_id, "s1", "strength_training", date(2026, 6, 8), 45),
            _activity(self.user_id, "r2", "running", date(2026, 6, 7), 35),
            _activity(self.user_id, "s2", "fitness_equipment", date(2026, 6, 5), 50),
            _activity(self.user_id, "b1", "boxing", date(2026, 6, 4), 60),
            _activity(self.user_id, "w1", "walking", date(2026, 6, 8), 40),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(95, signal.running_minutes_7d)          # 60 + 35
        self.assertEqual(195, signal.other_activity_minutes_7d)  # 45 + 50 + 60 + 40
        self.assertEqual(5, signal.total_hard_sessions_7d)       # 2 runs + 2 strength + 1 boxing

    def test_rationale_not_empty(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "r1", "running", date(2026, 6, 9), 60),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertTrue(len(signal.rationale) > 0)

    def test_activity_type_case_insensitive(self):
        repo = FakeActivitySource((
            _activity(self.user_id, "r1", "Running", date(2026, 6, 9), 45),
            _activity(self.user_id, "s1", "Strength_Training", date(2026, 6, 8), 40),
        ))
        signal = HolisticLoadService(repo).weekly_signal(self.user_id, self.as_of)

        self.assertEqual(45, signal.running_minutes_7d)
        self.assertEqual(40, signal.other_activity_minutes_7d)
        self.assertEqual(2, signal.total_hard_sessions_7d)



if __name__ == "__main__":
    unittest.main()
