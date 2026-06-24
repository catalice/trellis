from __future__ import annotations

import unittest
from datetime import date, time
from uuid import uuid4

from trellis.training import (
    Intensity,
    PlanMode,
    PlanningRequest,
    SessionBlock,
    SessionKind,
    SocialRunStatus,
    TrainingPlanner,
    TrainingSession,
    UnsafePlanError,
    Weekday,
)


def _run_session(day: Weekday, kind: SessionKind = SessionKind.EASY_RUN, intensity: Intensity = Intensity.EASY) -> TrainingSession:
    return TrainingSession(
        id=uuid4(),
        day=day,
        kind=kind,
        title=kind.value.replace("_", " ").title(),
        intensity=intensity,
        blocks=(
            SessionBlock("Run", 30, ("Run for 30 minutes.",)),
        ),
    )


def _hard_run(day: Weekday) -> TrainingSession:
    return _run_session(day, SessionKind.HARD_RUN, Intensity.HARD)


class TrainingPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = TrainingPlanner()
        self.week_start = date(2026, 6, 8)

    def test_build_week_always_has_strength_anchors(self):
        plan = self.planner.plan(PlanningRequest(week_start=self.week_start))

        strength = [s for s in plan.sessions if s.kind == SessionKind.STRENGTH]
        self.assertEqual({Weekday.MONDAY, Weekday.THURSDAY}, {s.day for s in strength})
        self.assertTrue(all(s.fixed_anchor for s in strength))

    def test_planner_does_not_generate_social_run_itself(self):
        # Social run content is Claude's responsibility — planner never creates it
        plan = self.planner.plan(PlanningRequest(week_start=self.week_start))
        self.assertFalse(any(s.kind == SessionKind.SOCIAL_RUN for s in plan.sessions))

    def test_social_run_from_claude_sessions_is_merged(self):
        social = _run_session(Weekday.WEDNESDAY, SessionKind.SOCIAL_RUN, Intensity.HARD)
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                claude_sessions=(social,),
            )
        )
        result = [s for s in plan.sessions if s.kind == SessionKind.SOCIAL_RUN]
        self.assertEqual(1, len(result))
        self.assertEqual(Weekday.WEDNESDAY, result[0].day)

    def test_social_run_that_is_not_hard_passes_through_unchanged(self):
        moderate_social = _run_session(Weekday.WEDNESDAY, SessionKind.SOCIAL_RUN, Intensity.MODERATE)
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                claude_sessions=(moderate_social,),
            )
        )
        social = next(s for s in plan.sessions if s.kind == SessionKind.SOCIAL_RUN)
        self.assertEqual(Intensity.MODERATE, social.intensity)

    def test_claude_sessions_are_merged_into_plan(self):
        long_run = _run_session(Weekday.SUNDAY, SessionKind.LONG_RUN)
        easy_run = _run_session(Weekday.FRIDAY)

        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                claude_sessions=(long_run, easy_run),
            )
        )

        kinds = {s.kind for s in plan.sessions}
        self.assertIn(SessionKind.LONG_RUN, kinds)
        self.assertIn(SessionKind.EASY_RUN, kinds)

    def test_claude_session_skipped_if_day_is_occupied_by_anchor(self):
        conflict = _run_session(Weekday.MONDAY, SessionKind.EASY_RUN)

        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                claude_sessions=(conflict,),
            )
        )

        monday_sessions = [s for s in plan.sessions if s.day == Weekday.MONDAY]
        self.assertEqual(1, len(monday_sessions))
        self.assertEqual(SessionKind.STRENGTH, monday_sessions[0].kind)

    def test_validation_rejects_two_hard_runs(self):
        with self.assertRaises(UnsafePlanError):
            self.planner.plan(
                PlanningRequest(
                    week_start=self.week_start,
                    claude_sessions=(
                        _run_session(Weekday.WEDNESDAY, SessionKind.SOCIAL_RUN, Intensity.HARD),
                        _hard_run(Weekday.FRIDAY),
                    ),
                )
            )

    def test_claude_session_on_strength_day_is_silently_skipped(self):
        # occupied_days prevents claude_sessions from overriding anchors
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                social_status=SocialRunStatus.PREDECLINED,
                claude_sessions=(_hard_run(Weekday.MONDAY),),
            )
        )
        monday_sessions = [s for s in plan.sessions if s.day == Weekday.MONDAY]
        self.assertEqual(1, len(monday_sessions))
        self.assertEqual(SessionKind.STRENGTH, monday_sessions[0].kind)

    def test_social_run_block_total_matches_sum_of_parts(self):
        social = TrainingSession(
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
        plan = self.planner.plan(PlanningRequest(
            week_start=self.week_start,
            claude_sessions=(social,),
        ))
        result = next(s for s in plan.sessions if s.kind == SessionKind.SOCIAL_RUN)
        self.assertEqual(sum(b.duration_minutes for b in result.blocks), result.total_minutes)
        self.assertGreater(len(result.blocks), 1)

    def test_deload_plan_has_strength_anchors(self):
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                mode=PlanMode.DELOAD,
                social_status=SocialRunStatus.PREDECLINED,
            )
        )

        strength = [s for s in plan.sessions if s.kind == SessionKind.STRENGTH]
        self.assertEqual(2, len(strength))
        self.assertFalse(any(s.intensity == Intensity.HARD for s in plan.sessions))

    def test_deload_social_run_intensity_passes_through_from_claude(self):
        # Claude is responsible for making deload social runs moderate — planner passes through unchanged
        moderate_social = _run_session(Weekday.WEDNESDAY, SessionKind.SOCIAL_RUN, Intensity.MODERATE)
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                mode=PlanMode.DELOAD,
                claude_sessions=(moderate_social,),
            )
        )
        social = next(s for s in plan.sessions if s.kind == SessionKind.SOCIAL_RUN)
        self.assertEqual(Intensity.MODERATE, social.intensity)

    def test_holiday_plan_has_no_anchors(self):
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                mode=PlanMode.HOLIDAY,
            )
        )

        self.assertEqual(PlanMode.HOLIDAY, plan.mode)
        self.assertEqual(0, len(plan.sessions))

    def test_holiday_plan_merges_claude_sessions(self):
        easy = _run_session(Weekday.TUESDAY)
        plan = self.planner.plan(
            PlanningRequest(
                week_start=self.week_start,
                mode=PlanMode.HOLIDAY,
                claude_sessions=(easy,),
            )
        )

        self.assertEqual(1, len(plan.sessions))
        self.assertFalse(any(s.intensity == Intensity.HARD for s in plan.sessions))
        self.assertIn("not a backlog", plan.rationale[0])


if __name__ == "__main__":
    unittest.main()
