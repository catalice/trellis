from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from uuid import uuid4

from trellis.health import GarminActivityRecord
from trellis.session_completion import SessionCompletion, SessionCompletionService
from trellis.training import (
    Intensity,
    PlanningRequest,
    SessionBlock,
    SessionKind,
    TrainingPlanner,
    TrainingSession,
    Weekday,
)

# Week of Mon 2026-06-08
WEEK_START = date(2026, 6, 8)
AS_OF      = date(2026, 6, 14)  # Sunday — full week visible
USER_ID    = uuid4()

# Sessions in the default plan:
MON = date(2026, 6, 8)   # Strength
TUE = date(2026, 6, 9)   # Mobility (no matching kind)
WED = date(2026, 6, 10)  # Social Run
THU = date(2026, 6, 11)  # Strength
FRI = date(2026, 6, 12)  # Easy Run
SAT = date(2026, 6, 13)  # (no planned session)
SUN = date(2026, 6, 14)  # Long Run


def _make_default_plan():
    """Build the canonical test plan with the full session set via claude_sessions."""
    return TrainingPlanner().plan(PlanningRequest(
        week_start=WEEK_START,
        claude_sessions=(
            TrainingSession(
                id=uuid4(), day=Weekday.TUESDAY, kind=SessionKind.MOBILITY,
                title="mobility", intensity=Intensity.EASY,
                blocks=(SessionBlock("Mobility", 30, ("Stretch and mobilise.",)),),
            ),
            TrainingSession(
                id=uuid4(), day=Weekday.WEDNESDAY, kind=SessionKind.SOCIAL_RUN,
                title="social run", intensity=Intensity.HARD,
                blocks=(
                    SessionBlock("Activation", 10, ("Drills.",)),
                    SessionBlock("Run", 45, ("Run with the group.",)),
                    SessionBlock("Cool-down", 10, ("Cool down.",)),
                ),
            ),
            TrainingSession(
                id=uuid4(), day=Weekday.FRIDAY, kind=SessionKind.EASY_RUN,
                title="easy run", intensity=Intensity.EASY,
                blocks=(SessionBlock("Run", 35, ("Run easy.",)),),
            ),
            TrainingSession(
                id=uuid4(), day=Weekday.SUNDAY, kind=SessionKind.LONG_RUN,
                title="long run", intensity=Intensity.EASY,
                blocks=(SessionBlock("Run", 60, ("Run long and slow.",)),),
            ),
        ),
    ))


def _epoch(d: date, hour: int = 10) -> int:
    return int(datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).timestamp())


def _activity(atype: str, on: date, activity_id: str = "1") -> GarminActivityRecord:
    return GarminActivityRecord(
        user_id=USER_ID,
        activity_id=activity_id,
        name="test",
        activity_type=atype,
        start_time_epoch_seconds=_epoch(on),
    )


def _service(activities: list, plan=None, stored: list | None = None):
    if plan is None:
        plan = _make_default_plan()
    repo = MagicMock()
    repo.list_for_week.return_value = stored or []
    repo.save.side_effect = lambda c: c
    plan_source = MagicMock()
    plan_source.latest_active.return_value = plan
    activity_source = MagicMock()
    activity_source.latest_activities.return_value = tuple(activities)
    svc = SessionCompletionService(
        repository=repo,
        activity_source=activity_source,
        plan_source=plan_source,
    )
    return svc, repo, plan


class TestMatchWeek:
    def test_no_plan_returns_empty(self):
        svc, _, _ = _service([])
        svc.plan_source.latest_active.return_value = None
        assert svc.match_week(USER_ID, WEEK_START, AS_OF) == []

    def test_no_activities_returns_empty(self):
        svc, _, _ = _service([])
        assert svc.match_week(USER_ID, WEEK_START, AS_OF) == []

    def test_run_matched_same_day(self):
        svc, repo, _ = _service([_activity("running", FRI)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert len(results) == 1
        assert results[0].session_kind == "easy_run"
        assert results[0].planned_on == FRI
        repo.save.assert_called_once()

    def test_run_matched_day_before(self):
        # Run on Thursday; easy_run is Friday (delta=-1). Strength on Thursday won't grab it.
        svc, _, _ = _service([_activity("running", THU)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        run_completions = [r for r in results if r.session_kind == "easy_run"]
        assert len(run_completions) == 1
        assert run_completions[0].planned_on == FRI

    def test_run_matched_day_after(self):
        # Run on Saturday; long_run is Sunday (delta=-1 from Sunday's perspective)
        svc, _, _ = _service([_activity("running", SAT)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        long_run = [r for r in results if r.session_kind == "long_run"]
        assert len(long_run) == 1
        assert long_run[0].planned_on == SUN

    def test_strength_matched_same_day(self):
        svc, _, _ = _service([_activity("strength_training", MON)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        strength = [r for r in results if r.session_kind == "strength"]
        assert len(strength) == 1
        assert strength[0].planned_on == MON

    def test_no_double_counting(self):
        # One run on Wednesday — can only complete ONE session (social_run), not also easy_run
        svc, _, _ = _service([_activity("running", WED)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        run_results = [r for r in results if "run" in r.session_kind]
        assert len(run_results) == 1

    def test_activity_type_normalization_treadmill(self):
        svc, _, _ = _service([_activity("treadmill_running", FRI)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert any(r.session_kind == "easy_run" for r in results)

    def test_activity_type_normalization_trail(self):
        svc, _, _ = _service([_activity("trail_running", SUN)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert any(r.session_kind == "long_run" for r in results)

    def test_future_session_not_matched(self):
        # as_of = Friday; Sunday long_run is in the future and must not match
        as_of = FRI
        # Put a run on Saturday, adjacent to Sunday — should still not match
        svc, _, _ = _service([_activity("running", SAT)])
        results = svc.match_week(USER_ID, WEEK_START, as_of)
        assert not any(r.session_kind == "long_run" for r in results)

    def test_future_activity_excluded(self):
        # Activity after as_of is not usable
        next_week = date(2026, 6, 15)
        svc, _, _ = _service([_activity("running", next_week)])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert results == []

    def test_same_day_beats_adjacent(self):
        # Run on Friday (same day as easy_run) and run on Saturday (adjacent to long_run Sun)
        svc, _, _ = _service([
            _activity("running", FRI, "100"),
            _activity("running", SAT, "200"),
        ])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        run_kinds = {r.session_kind for r in results if "run" in r.session_kind}
        assert "easy_run" in run_kinds
        assert "long_run" in run_kinds
        easy = next(r for r in results if r.session_kind == "easy_run")
        long = next(r for r in results if r.session_kind == "long_run")
        assert easy.garmin_activity_id != long.garmin_activity_id

    def test_activity_id_stored_as_int(self):
        svc, _, _ = _service([_activity("running", FRI, "42")])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert results[0].garmin_activity_id == 42

    def test_non_numeric_activity_id_stored_as_none(self):
        svc, _, _ = _service([_activity("running", FRI, "garmin-abc")])
        results = svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert results[0].garmin_activity_id is None

    def test_saves_to_repository(self):
        svc, repo, _ = _service([_activity("running", FRI)])
        svc.match_week(USER_ID, WEEK_START, AS_OF)
        assert repo.save.called


class TestFormatWeekCompletion:
    def test_uses_stored_completions(self):
        # No fresh activities, but DB has a stored completion — should show ✓
        plan = _make_default_plan()
        run_session = next(s for s in plan.sessions if s.kind.value == "easy_run")
        stored = SessionCompletion(
            id=uuid4(),
            user_id=USER_ID,
            plan_id=plan.id,
            session_id=run_session.id,
            garmin_activity_id=None,
            session_kind="easy_run",
            planned_on=FRI,
            completed_at=None,
            created_at=datetime.now(timezone.utc),
        )
        svc, _, _ = _service([], plan=plan, stored=[stored])
        result = svc.format_week_completion(USER_ID, WEEK_START, AS_OF)
        assert "✓" in result
        assert run_session.title in result

    def test_no_plan_returns_message(self):
        svc, _, _ = _service([])
        svc.plan_source.latest_active.return_value = None
        result = svc.format_week_completion(USER_ID, WEEK_START, AS_OF)
        assert "No training plan" in result

    def test_unmatched_session_shows_dash(self):
        svc, _, _ = _service([])
        result = svc.format_week_completion(USER_ID, WEEK_START, AS_OF)
        assert "—" in result

    def test_format_contains_week_header(self):
        svc, _, _ = _service([])
        result = svc.format_week_completion(USER_ID, WEEK_START, AS_OF)
        assert WEEK_START.isoformat() in result

    def test_reads_stored_completions_from_repo(self):
        svc, repo, _ = _service([])
        svc.format_week_completion(USER_ID, WEEK_START, AS_OF)
        repo.list_for_week.assert_called_once_with(USER_ID, WEEK_START)
