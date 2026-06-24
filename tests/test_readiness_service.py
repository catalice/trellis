from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from trellis.garmin.models import GarminDailyHealth
from trellis.health import GarminDailyHealthRecord, SelfHealthReport
from trellis.readiness import ReadinessBand
from trellis.readiness_service import ReadinessService


class FakeHealthRepository:
    def __init__(self) -> None:
        self.daily: dict[tuple[UUID, date], GarminDailyHealthRecord] = {}
        self.reports: list[SelfHealthReport] = []

    def upsert_daily(self, record: GarminDailyHealthRecord) -> None:
        self.daily[(record.user_id, record.observed_on)] = record

    def add_report(self, report: SelfHealthReport) -> None:
        self.reports.append(report)

    def get_daily_health(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> GarminDailyHealthRecord | None:
        return self.daily.get((user_id, observed_on))

    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None:
        matches = [
            record
            for (record_user_id, _), record in self.daily.items()
            if record_user_id == user_id
        ]
        return max(matches, key=lambda record: record.observed_on) if matches else None

    def resting_heart_rate_baseline(
        self,
        user_id: UUID,
        *,
        before: date,
        days: int = 60,
    ) -> int | None:
        return 52

    def list_self_reports(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        matches = [
            report
            for report in self.reports
            if report.user_id == user_id and report.observed_on == observed_on
        ]
        return tuple(sorted(matches, key=lambda report: report.reported_at))


class ReadinessServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = uuid4()
        self.today = date(2026, 6, 7)
        self.repository = FakeHealthRepository()
        self.service = ReadinessService(self.repository)

    def test_computes_today_from_garmin_daily_health(self):
        self.repository.upsert_daily(
            self._daily_health(
                sleep_score=86,
                sleep_duration_minutes=470,
                body_battery_end=82,
                resting_heart_rate=51,
                hrv_last_night=54,
                hrv_weekly_average=50,
                average_stress=24,
            )
        )

        snapshot = self.service.today(self.user_id, on_date=self.today)

        self.assertEqual(self.today, snapshot.requested_on)
        self.assertEqual(self.today, snapshot.source_health_date)
        self.assertFalse(snapshot.used_latest_health_fallback)
        self.assertTrue(snapshot.has_garmin_data)
        self.assertEqual(ReadinessBand.STRONG, snapshot.band)
        self.assertEqual("high", snapshot.confidence)
        self.assertNotIn("resting_heart_rate", snapshot.missing_metrics)
        self.assertTrue(
            any(contribution.name == "sleep" for contribution in snapshot.contributions)
        )
        self.assertIn("Garmin source: 2026-06-07.", snapshot.data_lines)
        self.assertIn("Sleep: 7h 50m, score 86.", snapshot.data_lines)
        self.assertIn("Body battery: 82.", snapshot.data_lines)
        self.assertIn("Self-report: missing.", snapshot.data_lines)

    def test_uses_latest_garmin_health_when_today_is_not_synced_yet(self):
        yesterday = self.today.replace(day=6)
        self.repository.upsert_daily(
            self._daily_health(
                observed_on=yesterday,
                sleep_score=80,
                body_battery_end=70,
                average_stress=35,
            )
        )

        snapshot = self.service.today(self.user_id, on_date=self.today)

        self.assertEqual(self.today, snapshot.requested_on)
        self.assertEqual(yesterday, snapshot.source_health_date)
        self.assertTrue(snapshot.used_latest_health_fallback)

    def test_latest_self_report_supplements_garmin_data(self):
        self.repository.upsert_daily(
            self._daily_health(
                sleep_score=75,
                body_battery_end=62,
                average_stress=35,
            )
        )
        morning = self._report(
            energy_score=4,
            body_score=4,
            life_load_score=8,
            reported_at=datetime(2026, 6, 7, 8, tzinfo=timezone.utc),
        )
        later = self._report(
            energy_score=8,
            body_score=7,
            life_load_score=4,
            reported_at=datetime(2026, 6, 7, 10, tzinfo=timezone.utc),
        )
        self.repository.add_report(morning)
        self.repository.add_report(later)

        snapshot = self.service.today(self.user_id, on_date=self.today)

        self.assertEqual(later.id, snapshot.self_report_id)
        self.assertTrue(
            any(contribution.name == "self_report" for contribution in snapshot.contributions)
        )
        self.assertNotIn("self_report", snapshot.missing_metrics)

    def test_self_report_only_snapshot_is_low_confidence(self):
        report = self._report(
            energy_score=6,
            body_score=5,
            life_load_score=7,
            sleep_minutes=420,
        )
        self.repository.add_report(report)

        snapshot = self.service.today(self.user_id, on_date=self.today)

        self.assertFalse(snapshot.has_garmin_data)
        self.assertEqual(report.id, snapshot.self_report_id)
        self.assertEqual("low", snapshot.confidence)
        self.assertIn("body_battery", snapshot.missing_metrics)
        self.assertIn("Readiness is usable but low-confidence", snapshot.rationale)

    def _daily_health(
        self,
        *,
        observed_on: date | None = None,
        sleep_score: int | None = None,
        sleep_duration_minutes: int | None = None,
        body_battery_end: int | None = None,
        resting_heart_rate: int | None = None,
        hrv_last_night: float | None = None,
        hrv_weekly_average: float | None = None,
        average_stress: int | None = None,
    ) -> GarminDailyHealthRecord:
        return GarminDailyHealthRecord.from_garmin(
            self.user_id,
            GarminDailyHealth(
                date=observed_on or self.today,
                sleep_score=sleep_score,
                sleep_duration_minutes=sleep_duration_minutes,
                body_battery_end=body_battery_end,
                resting_heart_rate=resting_heart_rate,
                hrv_last_night=hrv_last_night,
                hrv_weekly_average=hrv_weekly_average,
                average_stress=average_stress,
            ),
        )

    def _report(
        self,
        *,
        energy_score: int | None = None,
        body_score: int | None = None,
        life_load_score: int | None = None,
        sleep_minutes: int | None = None,
        reported_at: datetime | None = None,
    ) -> SelfHealthReport:
        return SelfHealthReport(
            user_id=self.user_id,
            observed_on=self.today,
            energy_score=energy_score,
            body_score=body_score,
            life_load_score=life_load_score,
            sleep_minutes=sleep_minutes,
            reported_at=reported_at or datetime(2026, 6, 7, 8, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
