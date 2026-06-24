from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.readiness import ReadinessBand
from trellis.readiness_service import ReadinessSnapshot
from trellis.run_targets import (
    HeartRateRange,
    PaceRange,
    RunTarget,
    RunTargetCalibration,
)
from trellis.training import (
    Intensity,
    PlanMode,
    PlanningRequest,
    SessionBlock,
    SessionKind,
    SocialRunStatus,
    TrainingPlanner,
    TrainingSession,
    Weekday,
)
from trellis.training_history import TrainingHistorySummary
from trellis.training_service import TrainingAction, TrainingOperation, TrainingService
from trellis.training_targets import TrainingTargetFormatter


class FakeTrainingRepository:
    def __init__(self):
        self.saved = []

    def save_active(self, user_id, plan):
        self.saved.append((user_id, plan))
        return plan

    def latest_active(self, user_id, week_start):
        for saved_user_id, plan in reversed(self.saved):
            if saved_user_id == user_id and plan.week_start == week_start:
                return plan
        return None


class FakeProjection:
    def __init__(self):
        self.plans = []

    def write(self, plan):
        self.plans.append(plan)


class FakeReadiness:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def today(self, user_id, *, on_date):
        return self.snapshot


class FakeRunTargets:
    def __init__(self, calibration):
        self.calibration = calibration

    def calibrate(self, user_id):
        return self.calibration


class FakeTrainingHistory:
    def __init__(self, summary):
        self.summary = summary

    def summarize(self, user_id, *, as_of):
        return self.summary


def _operation(action: TrainingAction, **kwargs) -> TrainingOperation:
    return TrainingOperation(
        action=action,
        summary=action.value,
        detail=kwargs.get("detail", False),
        session_kind=kwargs.get("session_kind"),
        run_count=kwargs.get("run_count"),
        strength_days=kwargs.get("strength_days", ()),
        replacement_day=kwargs.get("replacement_day"),
        replacement_time_of_day=kwargs.get("replacement_time_of_day"),
        mode=kwargs.get("mode"),
        clarification_question=kwargs.get("clarification_question"),
    )


def _easy_run_session(day: Weekday = Weekday.FRIDAY) -> TrainingSession:
    return TrainingSession(
        id=uuid4(),
        day=day,
        kind=SessionKind.EASY_RUN,
        title="Easy run",
        intensity=Intensity.EASY,
        blocks=(
            SessionBlock("Activation", 10, ("Warm up.",)),
            SessionBlock("Run", 35, ("Run easy.",)),
            SessionBlock("Cool-down and mobility", 10, ("Cool down.",)),
        ),
    )


class TrainingServiceTest(unittest.TestCase):
    def setUp(self):
        self.repository = FakeTrainingRepository()
        self.projection = FakeProjection()
        self.user_id = uuid4()
        self.service = TrainingService(
            self.repository,
            self.projection,
            TrainingPlanner(),
            ZoneInfo("Europe/Madrid"),
        )
        # Tuesday 09 Jun 2026
        self.now = datetime(2026, 6, 9, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        self.week_start = date(2026, 6, 8)

    def test_create_plan_saves_plan_with_strength_sessions(self):
        response = self.service.create_plan(self.user_id, self.now)

        self.assertIn("Training plan", response)
        saved = self.repository.latest_active(self.user_id, self.week_start)
        self.assertIsNotNone(saved)
        kinds = {s.kind for s in saved.sessions}
        self.assertIn(SessionKind.STRENGTH, kinds)

    def test_show_plan_returns_existing_without_saving_revision(self):
        self.service.create_plan(self.user_id, self.now)
        before_count = len(self.repository.saved)

        response = self.service.execute_training_operation(
            self.user_id, _operation(TrainingAction.SHOW_PLAN), self.now
        )

        self.assertIn("Training plan", response)
        self.assertEqual(before_count, len(self.repository.saved))

    def test_show_plan_returns_message_when_no_plan_exists(self):
        response = self.service.execute_training_operation(
            self.user_id, _operation(TrainingAction.SHOW_PLAN), self.now
        )

        self.assertIn("No active training plan", response)

    def test_clarify_operation_returns_question(self):
        response = self.service.execute_training_operation(
            self.user_id,
            _operation(TrainingAction.CLARIFY, clarification_question="Did you mean this?"),
            self.now,
        )
        self.assertIn("Did you mean this?", response)

    def test_sunday_create_plan_targets_next_week(self):
        sunday_now = datetime(2026, 6, 14, 20, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        response = self.service.create_plan(self.user_id, sunday_now)

        next_monday = date(2026, 6, 15)
        saved = self.repository.latest_active(self.user_id, next_monday)
        self.assertIsNotNone(saved)
        self.assertIn(str(next_monday), response)

    def test_deload_week_creates_deload_plan(self):
        response = self.service.create_plan(self.user_id, self.now, mode="deload")

        self.assertIn("deload", response)
        saved = self.repository.latest_active(self.user_id, self.week_start)
        self.assertEqual(PlanMode.DELOAD, saved.mode)

    def test_plan_creation_uses_training_history_for_run_targets(self):
        history = TrainingHistorySummary(
            runs_28d=16,
            distance_28d_km=120.0,
            minutes_28d=640,
            longest_run_84d_minutes=75,
            longest_run_84d_km=10.5,
            longest_run_anchor_minutes=75,
            rationale=("Solid 28-day base.",),
        )
        service = TrainingService(
            self.repository,
            self.projection,
            TrainingPlanner(),
            ZoneInfo("Europe/Madrid"),
            training_history=FakeTrainingHistory(history),
        )

        service.create_plan(self.user_id, self.now)

        saved = self.repository.latest_active(self.user_id, self.week_start)
        self.assertIsNotNone(saved)
        # History rationale should appear in the saved plan
        self.assertIn("Solid 28-day base.", saved.rationale)

    def test_today_training_returns_no_plan_message(self):
        response = self.service.execute_training_operation(
            self.user_id, _operation(TrainingAction.TODAY_TRAINING), self.now
        )
        self.assertIn("No active training plan", response)

    def test_today_training_returns_sessions_for_today(self):
        self.service.create_plan(self.user_id, self.now)
        # Tuesday 9 Jun — should be no planned session (strength Mon/Thu, social Wed)
        response = self.service.execute_training_operation(
            self.user_id, _operation(TrainingAction.TODAY_TRAINING), self.now
        )
        self.assertIn("Today's training", response)

    def test_today_training_with_readiness_includes_recommendation(self):
        snapshot = ReadinessSnapshot(
            user_id=uuid4(),
            requested_on=date(2026, 6, 9),
            source_health_date=date(2026, 6, 9),
            used_latest_health_fallback=False,
            score=72,
            band=ReadinessBand.STEADY,
            confidence="high",
            contributions=(),
            rationale=("Good body battery.",),
            missing_metrics=(),
            data_lines=(),
        )
        service = TrainingService(
            self.repository,
            self.projection,
            TrainingPlanner(),
            ZoneInfo("Europe/Madrid"),
            readiness=FakeReadiness(snapshot),
        )
        service.create_plan(self.user_id, self.now)
        response = service.execute_training_operation(
            self.user_id, _operation(TrainingAction.TODAY_TRAINING), self.now
        )
        self.assertIn("Readiness", response)
        self.assertIn("72/100", response)

    def test_get_plan_text_returns_no_plan_message_when_empty(self):
        result = self.service.get_plan_text(self.user_id, self.now)
        self.assertIn("No active training plan", result)
        self.assertIn("create_plan", result)

    def test_get_plan_text_returns_existing_plan(self):
        self.service.create_plan(self.user_id, self.now)
        result = self.service.get_plan_text(self.user_id, self.now)
        self.assertIn("Training plan", result)
        self.assertIn(str(self.week_start), result)

    def test_get_session_text_returns_no_plan_when_empty(self):
        result = self.service.get_session_text(self.user_id, "strength", self.now)
        self.assertEqual("No active training plan this week.", result)

    def test_get_session_text_returns_strength_detail(self):
        self.service.create_plan(self.user_id, self.now)
        result = self.service.get_session_text(self.user_id, "strength", self.now)
        self.assertIn("Personal training: strength", result)

    def test_get_session_text_with_run_targets_includes_watch_steps(self):
        calibration = RunTargetCalibration(
            easy_run=RunTarget(
                name="easy_run",
                calibrated=True,
                confidence=0.8,
                pace_range=PaceRange(slow_seconds_per_km=445, fast_seconds_per_km=405),
                heart_rate_range=HeartRateRange(138, 152),
                reasons=(),
                sample_size=6,
            ),
            long_run=RunTarget(
                name="long_run",
                calibrated=True,
                confidence=0.8,
                pace_range=PaceRange(slow_seconds_per_km=490, fast_seconds_per_km=425),
                heart_rate_range=HeartRateRange(136, 150),
                reasons=(),
                sample_size=4,
            ),
            interval=RunTarget(
                name="interval",
                calibrated=True,
                confidence=0.9,
                pace_range=PaceRange(slow_seconds_per_km=370, fast_seconds_per_km=345),
                heart_rate_range=HeartRateRange(158, 170),
                reasons=(),
                sample_size=5,
            ),
        )
        service = TrainingService(
            self.repository,
            self.projection,
            TrainingPlanner(),
            ZoneInfo("Europe/Madrid"),
            run_targets=FakeRunTargets(calibration),
            target_formatter=TrainingTargetFormatter(),
        )

        # Save a plan that includes a social run (has a Run block)
        social_run = TrainingSession(
            id=uuid4(),
            day=Weekday.WEDNESDAY,
            kind=SessionKind.SOCIAL_RUN,
            title="Social run",
            intensity=Intensity.HARD,
            blocks=(
                SessionBlock("Activation", 10, ("Drills.",)),
                SessionBlock("Run", 45, ("Run with group.",)),
                SessionBlock("Cool-down", 10, ("Stretch.",)),
            ),
        )
        plan = TrainingPlanner().plan(PlanningRequest(
            week_start=self.week_start,
            claude_sessions=(social_run,),
        ))
        self.repository.save_active(self.user_id, plan)
        self.projection.write(plan)
        result = service.get_session_text(self.user_id, "social_run", self.now)
        self.assertIn("Watch steps", result)

    def test_explain_plan_returns_structured_facts(self):
        self.service.create_plan(self.user_id, self.now)
        response = self.service.execute_training_operation(
            self.user_id, _operation(TrainingAction.EXPLAIN_PLAN), self.now
        )
        self.assertIn("Operation: explain_plan", response)
        self.assertIn("Run count:", response)
        self.assertIn("Total planned time:", response)

    def test_today_response_returns_sessions_for_today(self):
        self.service.create_plan(self.user_id, self.now)
        response = self.service.today_response(self.user_id, self.now)
        self.assertIn("Today's training", response)


if __name__ == "__main__":
    unittest.main()
