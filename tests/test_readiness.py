from __future__ import annotations

import unittest
from datetime import date, timedelta

from trellis.readiness import (
    DailyReadinessInput,
    ReadinessBand,
    ReadinessCalculator,
    SelfReport,
)


class ReadinessCalculatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = ReadinessCalculator()
        self.today = date(2026, 6, 7)

    def test_strong_day_has_transparent_positive_contributions(self):
        result = self.calculator.assess(
            DailyReadinessInput(
                date=self.today,
                sleep_duration_minutes=470,
                sleep_score=86,
                body_battery=82,
                resting_heart_rate=51,
                resting_heart_rate_baseline=54,
                hrv_last_night=54,
                hrv_baseline=50,
                average_stress=24,
                self_report=SelfReport(
                    energy=8,
                    body=8,
                    life_load=3,
                    soreness=2,
                ),
            )
        )

        self.assertEqual(100, result.score)
        self.assertEqual(ReadinessBand.STRONG, result.band)
        self.assertEqual("high", result.confidence)
        self.assertTrue(any("Sleep is strong" in item for item in result.rationale))
        self.assertTrue(any(c.name == "sleep" and c.points > 0 for c in result.contributions))

    def test_missing_garmin_data_uses_self_report_without_pretending_precision(self):
        result = self.calculator.assess(
            DailyReadinessInput(
                date=self.today,
                self_report=SelfReport(
                    energy=6,
                    body=5,
                    life_load=7,
                    soreness=4,
                ),
            )
        )

        self.assertEqual(64, result.score)
        self.assertEqual(ReadinessBand.STEADY, result.band)
        self.assertEqual("low", result.confidence)
        self.assertIn("sleep", result.missing_metrics)
        self.assertIn("body_battery", result.missing_metrics)
        self.assertTrue(
            any("Readiness is usable but low-confidence" in item for item in result.rationale)
        )

    def test_one_bad_metric_does_not_dominate_when_other_signals_are_good(self):
        result = self.calculator.assess(
            DailyReadinessInput(
                date=self.today,
                sleep_score=91,
                body_battery=80,
                resting_heart_rate=53,
                resting_heart_rate_baseline=53,
                hrv_last_night=30,
                hrv_baseline=50,
                average_stress=22,
                self_report=SelfReport(
                    energy=8,
                    body=8,
                    life_load=2,
                    soreness=2,
                ),
            )
        )

        hrv = next(c for c in result.contributions if c.name == "hrv")

        self.assertEqual(-10, hrv.points)
        self.assertGreaterEqual(result.score, 80)
        self.assertEqual(ReadinessBand.READY, result.band)
        self.assertTrue(
            any("No single poor metric is allowed" in item for item in result.rationale)
        )

    def test_low_trend_reduces_score_more_than_single_day_noise(self):
        history = (
            self._past_result(3, 48),
            self._past_result(2, 50),
            self._past_result(1, 51),
        )

        result = self.calculator.assess(
            DailyReadinessInput(
                date=self.today,
                sleep_score=76,
                body_battery=62,
                resting_heart_rate=55,
                resting_heart_rate_baseline=53,
                hrv_last_night=45,
                hrv_baseline=50,
                average_stress=42,
                self_report=SelfReport(
                    energy=6,
                    body=6,
                    life_load=5,
                    soreness=4,
                ),
            ),
            history=history,
        )

        trend = next(c for c in result.contributions if c.name == "trend")

        self.assertEqual(-6, trend.points)
        self.assertEqual(79, result.score)
        self.assertIn("Last 3 days average 49.7", trend.rationale)

    def test_normalized_health_objects_can_be_adapted_without_garmin_dependency(self):
        class NormalizedHealth:
            sleep_score = 80
            sleep_duration_minutes = 440
            body_battery_maximum = 74
            resting_heart_rate = 54
            hrv_last_night = 48
            average_stress = 35

        input_data = DailyReadinessInput.from_normalized_health(
            NormalizedHealth(),
            on_date=self.today,
            resting_heart_rate_baseline=55,
            hrv_baseline=50,
        )

        result = self.calculator.assess(input_data)

        self.assertEqual(93, result.score)
        self.assertEqual(ReadinessBand.STRONG, result.band)

    def _past_result(self, days_ago: int, score: int):
        return self.calculator.assess(
            DailyReadinessInput(
                date=self.today - timedelta(days=days_ago),
                sleep_score=50,
                body_battery=45,
                self_report=SelfReport(energy=4, body=4, life_load=7, soreness=6),
            )
        ).with_score_for_test(score)


if __name__ == "__main__":
    unittest.main()
