from __future__ import annotations

import unittest
from datetime import date, time
from uuid import uuid4

from trellis.readiness import ReadinessBand, ReadinessContribution, ReadinessResult
from trellis.readiness_service import ReadinessSnapshot
from trellis.training import (
    Intensity,
    PlanningRequest,
    SessionBlock,
    SessionKind,
    SocialRunStatus,
    TrainingPlanner,
    TrainingSession,
    Weekday,
)
from trellis.training_readiness import (
    TrainingAdjustment,
    TrainingReadinessAdvisor,
)


def _hard_run(day: Weekday) -> TrainingSession:
    return TrainingSession(
        id=uuid4(),
        day=day,
        kind=SessionKind.HARD_RUN,
        title="hard run",
        intensity=Intensity.HARD,
        blocks=(SessionBlock("Run", 30, ("Run hard.",)),),
    )


def _easy_run(day: Weekday) -> TrainingSession:
    return TrainingSession(
        id=uuid4(),
        day=day,
        kind=SessionKind.EASY_RUN,
        title="easy run",
        intensity=Intensity.EASY,
        blocks=(SessionBlock("Run", 35, ("Run easy.",)),),
    )


def _long_run(day: Weekday) -> TrainingSession:
    return TrainingSession(
        id=uuid4(),
        day=day,
        kind=SessionKind.LONG_RUN,
        title="long run",
        intensity=Intensity.EASY,
        blocks=(SessionBlock("Run", 60, ("Run long and slow.",)),),
    )


class TrainingReadinessAdvisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.advisor = TrainingReadinessAdvisor()
        self.planner = TrainingPlanner()
        self.week_start = date(2026, 6, 8)

    def test_low_readiness_recommends_swapping_hard_run_without_mutating_plan(self):
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                social_status=SocialRunStatus.PREDECLINED,
                claude_sessions=(_hard_run(Weekday.WEDNESDAY),),
            )
        )
        original_sessions = plan.sessions

        recommendation = self.advisor.recommend(
            plan,
            self._readiness(
                date(2026, 6, 10),
                score=42,
                band=ReadinessBand.LOW,
            ),
        )

        self.assertEqual(TrainingAdjustment.SWAP, recommendation.action)
        self.assertEqual(date(2026, 6, 10), recommendation.on_date)
        self.assertEqual(SessionKind.HARD_RUN, recommendation.sessions[0].kind)
        self.assertIn("Today: replace it with 20-25 minutes easy", recommendation.suggested_change)
        self.assertIn("Move the hard run to Friday 12 Jun", recommendation.suggested_change)
        self.assertEqual(original_sessions, plan.sessions)

    def test_steady_readiness_recommends_reducing_hard_work(self):
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                social_status=SocialRunStatus.PREDECLINED,
                claude_sessions=(_hard_run(Weekday.WEDNESDAY),),
            )
        )

        recommendation = self.advisor.recommend(
            plan,
            self._readiness(
                date(2026, 6, 10),
                score=60,
                band=ReadinessBand.STEADY,
            ),
        )

        self.assertEqual(TrainingAdjustment.REDUCE, recommendation.action)
        self.assertIn("reduce the hard work", recommendation.suggested_change)

    def test_ready_readiness_keeps_easy_run(self):
        plan = self.planner.plan(PlanningRequest(
            week_start=self.week_start,
            claude_sessions=(_easy_run(Weekday.FRIDAY),),
        ))

        recommendation = self.advisor.recommend(
            plan,
            self._readiness(
                date(2026, 6, 12),
                score=78,
                band=ReadinessBand.READY,
            ),
        )

        self.assertEqual(TrainingAdjustment.KEEP, recommendation.action)
        self.assertEqual(SessionKind.EASY_RUN, recommendation.sessions[0].kind)
        self.assertEqual("Keep the planned session.", recommendation.suggested_change)

    def test_low_readiness_reduces_long_run(self):
        plan = self.planner.plan(PlanningRequest(
            week_start=self.week_start,
            claude_sessions=(_long_run(Weekday.SUNDAY),),
        ))

        recommendation = self.advisor.recommend(
            plan,
            self._readiness(
                date(2026, 6, 14),
                score=49,
                band=ReadinessBand.LOW,
            ),
        )

        self.assertEqual(TrainingAdjustment.REDUCE, recommendation.action)
        self.assertEqual(SessionKind.LONG_RUN, recommendation.sessions[0].kind)
        self.assertIn("30-40 minutes", recommendation.suggested_change)

    def test_day_without_training_returns_keep_with_empty_sessions(self):
        plan = self.planner.plan(PlanningRequest(week_start=self.week_start))

        recommendation = self.advisor.recommend(
            plan,
            self._readiness(
                date(2026, 6, 13),
                score=82,
                band=ReadinessBand.READY,
            ),
            on_date=date(2026, 6, 13),
        )

        self.assertEqual(TrainingAdjustment.KEEP, recommendation.action)
        self.assertFalse(recommendation.has_training_today)
        self.assertIn("Keep the day open", recommendation.suggested_change)

    def test_accepts_readiness_snapshot_from_service_layer(self):
        plan = self.planner.plan(PlanningRequest(
            week_start=self.week_start,
            claude_sessions=(_easy_run(Weekday.FRIDAY),),
        ))
        snapshot = ReadinessSnapshot(
            user_id=uuid4(),
            requested_on=date(2026, 6, 12),
            source_health_date=date(2026, 6, 12),
            used_latest_health_fallback=False,
            score=41,
            band=ReadinessBand.LOW,
            confidence="medium",
            contributions=(),
            rationale=("Body battery is low.",),
            missing_metrics=(),
            data_lines=("Body battery: 38.",),
        )

        recommendation = self.advisor.recommend(plan, snapshot)

        self.assertEqual(date(2026, 6, 12), recommendation.on_date)
        self.assertEqual(TrainingAdjustment.REDUCE, recommendation.action)
        self.assertIn("Body battery is low.", recommendation.explanation)
        self.assertIn("Body battery: 38.", recommendation.data_lines)

    @staticmethod
    def _readiness(
        on_date: date,
        *,
        score: int,
        band: ReadinessBand,
        confidence: str = "high",
    ) -> ReadinessResult:
        return ReadinessResult(
            date=on_date,
            score=score,
            band=band,
            confidence=confidence,
            contributions=(
                ReadinessContribution("test", 0, "test readiness"),
            ),
            rationale=("Readiness rationale.",),
            missing_metrics=(),
        )


if __name__ == "__main__":
    unittest.main()
