from __future__ import annotations

import unittest
from datetime import date
from uuid import uuid4

from trellis.cycle import CycleEvent, CycleService


class InMemoryCycleRepository:
    def __init__(self):
        self._events: list[CycleEvent] = []

    def record(self, event: CycleEvent) -> CycleEvent:
        self._events.append(event)
        return event

    def list_recent(self, user_id, *, limit=10) -> list[CycleEvent]:
        return [e for e in self._events if e.user_id == user_id][-limit:]

    def last_period_start(self, user_id) -> CycleEvent | None:
        matches = [
            e for e in self._events
            if e.user_id == user_id and e.event_type == "period_start"
        ]
        return matches[-1] if matches else None


class CycleServiceTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryCycleRepository()
        self.service = CycleService(self.repository)
        self.user_id = uuid4()

    def test_record_period_start_stores_event(self):
        event = self.service.record_period_start(self.user_id, date(2026, 6, 1))
        self.assertEqual("period_start", event.event_type)
        self.assertEqual(date(2026, 6, 1), event.occurred_on)

    def test_record_period_start_with_note(self):
        event = self.service.record_period_start(
            self.user_id, date(2026, 6, 1), note="heavy"
        )
        self.assertEqual("heavy", event.note)

    def test_record_observation_stores_event(self):
        event = self.service.record_observation(
            self.user_id, date(2026, 6, 3),
            note="tired today",
            symptoms=("fatigue", "cramps"),
        )
        self.assertEqual("observation", event.event_type)
        self.assertIn("fatigue", event.symptoms)
        self.assertIn("cramps", event.symptoms)

    def test_get_status_no_data(self):
        status = self.service.get_status(self.user_id, date(2026, 6, 10))
        self.assertIn("no period start", status)

    def test_get_status_day_1(self):
        self.service.record_period_start(self.user_id, date(2026, 6, 10))
        status = self.service.get_status(self.user_id, date(2026, 6, 10))
        self.assertIn("day 1", status)
        self.assertIn("menstruation", status)

    def test_get_status_follicular_phase(self):
        self.service.record_period_start(self.user_id, date(2026, 6, 1))
        status = self.service.get_status(self.user_id, date(2026, 6, 9))  # day 9
        self.assertIn("follicular", status)
        self.assertIn("day 9", status)

    def test_get_status_luteal_phase(self):
        self.service.record_period_start(self.user_id, date(2026, 5, 20))
        status = self.service.get_status(self.user_id, date(2026, 6, 10))  # day 22
        self.assertIn("luteal", status)
        self.assertIn("day 22", status)

    def test_get_status_overdue(self):
        self.service.record_period_start(self.user_id, date(2026, 5, 1))
        status = self.service.get_status(self.user_id, date(2026, 6, 10))  # day 41
        self.assertIn("day 41", status)
        self.assertIn("due", status)

    def test_most_recent_period_start_is_used(self):
        self.service.record_period_start(self.user_id, date(2026, 5, 1))
        self.service.record_period_start(self.user_id, date(2026, 6, 1))
        status = self.service.get_status(self.user_id, date(2026, 6, 10))
        self.assertIn("day 10", status)

    def test_period_start_includes_date_in_status(self):
        self.service.record_period_start(self.user_id, date(2026, 6, 1))
        status = self.service.get_status(self.user_id, date(2026, 6, 5))
        self.assertIn("2026-06-01", status)


if __name__ == "__main__":
    unittest.main()
