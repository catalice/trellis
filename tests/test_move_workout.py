"""
Deterministic tests for the Garmin structured-workout builder and CSV baseline.
No network — run offline with:  uv run pytest tests/test_training_workout.py -q
"""
from __future__ import annotations

import unittest
import uuid

from trellis.domain_move_service import (
    WorkoutSpecError,
    build_garmin_workout,
)


def _steps(workout: dict) -> list[dict]:
    return workout["workoutSegments"][0]["workoutSteps"]


class TestBuildGarminWorkout(unittest.TestCase):
    def test_simple_easy_run_by_time(self):
        w = build_garmin_workout({"name": "Easy 30", "steps": [
            {"kind": "run", "duration": "30min", "note": "conversational"},
        ]})
        self.assertEqual(w["workoutName"], "Easy 30")
        self.assertEqual(w["sportType"]["sportTypeKey"], "running")
        steps = _steps(w)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["type"], "ExecutableStepDTO")
        self.assertEqual(step["stepType"]["stepTypeKey"], "interval")
        self.assertEqual(step["endCondition"]["conditionTypeKey"], "time")
        self.assertEqual(step["endConditionValue"], 1800.0)   # 30min
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "no.target")
        self.assertEqual(step["description"], "conversational")

    def test_6x400m_intervals(self):
        w = build_garmin_workout({"name": "6x400m intervals", "steps": [
            {"kind": "warmup", "duration": "10min"},
            {"kind": "repeat", "times": 6, "steps": [
                {"kind": "interval", "distance": "400m", "pace": "4:20-4:40", "note": "fast"},
                {"kind": "recovery", "duration": "90s"},
            ]},
            {"kind": "cooldown", "duration": "10min"},
        ]})
        steps = _steps(w)
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["stepType"]["stepTypeKey"], "warmup")
        rpt = steps[1]
        self.assertEqual(rpt["type"], "RepeatGroupDTO")
        self.assertEqual(rpt["numberOfIterations"], 6)
        self.assertEqual(len(rpt["workoutSteps"]), 2)
        interval = rpt["workoutSteps"][0]
        self.assertEqual(interval["endCondition"]["conditionTypeKey"], "distance")
        self.assertEqual(interval["endConditionValue"], 400.0)
        self.assertEqual(interval["targetType"]["workoutTargetTypeKey"], "pace.zone")
        # slower pace = lower m/s; valueOne is the low bound
        self.assertLess(interval["targetValueOne"], interval["targetValueTwo"])
        recovery = rpt["workoutSteps"][1]
        self.assertEqual(recovery["stepType"]["stepTypeKey"], "recovery")
        self.assertEqual(recovery["endConditionValue"], 90.0)
        self.assertEqual(steps[2]["stepType"]["stepTypeKey"], "cooldown")

    def test_long_run_with_tempo_reps_by_time(self):
        w = build_garmin_workout({"name": "Long w/ tempo", "steps": [
            {"kind": "warmup", "duration": "15min"},
            {"kind": "repeat", "times": 3, "steps": [
                {"kind": "interval", "duration": "10min", "hr": "150-160", "note": "tempo"},
                {"kind": "recovery", "duration": "3min"},
            ]},
            {"kind": "cooldown", "duration": "10min"},
        ]})
        rpt = _steps(w)[1]
        self.assertEqual(rpt["numberOfIterations"], 3)
        tempo = rpt["workoutSteps"][0]
        self.assertEqual(tempo["endCondition"]["conditionTypeKey"], "time")
        self.assertEqual(tempo["endConditionValue"], 600.0)
        self.assertEqual(tempo["targetType"]["workoutTargetTypeKey"], "heart.rate.zone")
        self.assertEqual((tempo["targetValueOne"], tempo["targetValueTwo"]), (150.0, 160.0))

    def test_open_step_uses_lap_button(self):
        w = build_garmin_workout({"name": "Open", "steps": [{"kind": "warmup"}]})
        step = _steps(w)[0]
        self.assertEqual(step["endCondition"]["conditionTypeKey"], "lap.button")
        self.assertIsNone(step.get("endConditionValue"))

    def test_output_validates_against_garmin_model(self):
        """The real guarantee: our output is a VALID Garmin workout — built on and
        checked against garminconnect's own pydantic model, so the format can't
        silently drift from what Garmin accepts."""
        from garminconnect.workout import RunningWorkout
        specs = [
            {"name": "Easy", "steps": [{"kind": "run", "distance": "5km", "pace": "6:00-6:30"}]},
            {"name": "6x400", "steps": [
                {"kind": "warmup", "duration": "10min"},
                {"kind": "repeat", "times": 6, "steps": [
                    {"kind": "interval", "distance": "400m", "pace": "4:20-4:40"},
                    {"kind": "recovery", "duration": "90s"}]},
                {"kind": "cooldown", "duration": "10min"}]},
            {"name": "Tempo", "steps": [
                {"kind": "warmup", "duration": "15min"},
                {"kind": "interval", "duration": "20min", "hr": "150-165"},
                {"kind": "cooldown", "duration": "10min"}]},
        ]
        for spec in specs:
            RunningWorkout.model_validate(build_garmin_workout(spec))  # raises if invalid

    def test_distance_units_km_and_mi(self):
        w = build_garmin_workout({"name": "d", "steps": [
            {"kind": "run", "distance": "5km"},
            {"kind": "run", "distance": "1mi"},
        ]})
        steps = _steps(w)
        self.assertEqual(steps[0]["endConditionValue"], 5000.0)
        self.assertAlmostEqual(steps[1]["endConditionValue"], 1609.34, places=2)

    def test_continuous_unique_step_order(self):
        w = build_garmin_workout({"name": "o", "steps": [
            {"kind": "warmup", "duration": "5min"},
            {"kind": "repeat", "times": 2, "steps": [
                {"kind": "interval", "distance": "1km"},
                {"kind": "recovery", "duration": "2min"},
            ]},
            {"kind": "cooldown", "duration": "5min"},
        ]})
        orders = []
        def collect(steps):
            for s in steps:
                orders.append(s["stepOrder"])
                if s.get("type") == "RepeatGroupDTO":
                    collect(s["workoutSteps"])
        collect(_steps(w))
        self.assertEqual(len(orders), len(set(orders)))  # unique

    def test_malformed_specs_raise(self):
        for bad in (
            "not a dict",
            {"name": "x"},                                    # no steps
            {"name": "x", "steps": []},                        # empty
            {"steps": [{"kind": "bogus", "duration": "5min"}]},  # unknown kind
            {"steps": [{"kind": "repeat", "steps": [{"kind": "run"}]}]},  # repeat, no times
            {"steps": [{"kind": "run", "duration": "not-a-time"}]},       # bad duration
        ):
            with self.assertRaises(WorkoutSpecError):
                build_garmin_workout(bad)


if __name__ == "__main__":
    unittest.main()


class TestActivityVisibility(unittest.TestCase):
    """The coach must SEE every activity type (the Friday-strength bug: the old
    runs-only reader made 'my most recent workout' answer with an older run).
    The run LOG stays runs-only — it feeds the running baseline."""

    def _service(self, activities):
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        class FakeReader:
            def recent_activities(self, user_id, *, limit):
                return activities[:limit]
            def activity_detail(self, user_id, activity_id):
                raise RuntimeError("no detail in test")

        class FakeRepo:
            added = []
            def recent_runs(self, user_id, limit=200):
                return []
            def add_run(self, run):
                self.added.append(run)
                return run

        self.repo = FakeRepo()
        self.repo.added = []
        return MoveService(self.repo, goals=None, tz=ZoneInfo("Europe/Madrid"),
                           garmin_read=FakeReader())

    @staticmethod
    def _act(activity_type, name, epoch, distance=None, hr=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            activity_id="a1", name=name, activity_type=activity_type,
            start_time_epoch_seconds=epoch, distance_meters=distance,
            duration_milliseconds=3_600_000, average_heart_rate=hr,
            maximum_heart_rate=None,
        )

    def test_review_most_recent_includes_strength(self):
        svc = self._service([
            self._act("strength_training", "Strength", 1785492678, hr=116),
            self._act("running", "Morning Running", 1785446606, distance=3255.0),
        ])
        detail = svc.review_run(uuid.uuid4(), which=0)
        self.assertEqual(detail["overall"]["name"], "Strength")
        self.assertIsNone(detail["overall"]["distance_km"])
        self.assertEqual(detail["overall"]["avg_hr"], 116)

    def test_sync_upsert_never_touches_user_note(self):
        # THE logbook invariant (migration 017): the user's words live in
        # user_note and sync's upsert must never name that column.
        import inspect
        from trellis.infra_tracking import PostgresHealthRepository
        sql = inspect.getsource(PostgresHealthRepository.upsert_activity)
        self.assertNotIn("user_note", sql)


class TestSplitExtraction(unittest.TestCase):
    """Audit item 26: run-walk workouts have an EMPTY lap array; the old code
    fell through to splitSummaries (aggregates per type) and presented them as
    sequential laps — muddled nonsense. Typed chronological splits win now."""

    class _Detail:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def test_typed_splits_preferred_over_summaries(self):
        from trellis.domain_move_service import _extract_splits
        detail = self._Detail(
            splits=[],
            split_summaries={"splitSummaries": [
                {"splitType": "RWD_RUN", "noOfSplits": 14, "distance": 2110.0,
                 "duration": 1500.0, "averageHR": 152.0},
            ]},
            typed_splits={"splits": [
                {"type": "RWD_RUN", "distance": 640.0, "duration": 252.0,
                 "averageHR": 168.0, "maxHR": 191.0},
                {"type": "RWD_WALK", "distance": 90.0, "duration": 61.0,
                 "averageHR": 142.0},
            ]},
        )
        rows = _extract_splits(detail)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["type"], "run")
        self.assertEqual(rows[1]["type"], "walk")
        self.assertNotIn("count", rows[0])

    def test_summaries_marked_as_aggregates(self):
        from trellis.domain_move_service import _extract_splits
        detail = self._Detail(
            splits=[], typed_splits={},
            split_summaries={"splitSummaries": [
                {"splitType": "RWD_RUN", "noOfSplits": 14, "distance": 2110.0,
                 "duration": 1500.0, "averageHR": 152.0},
                {"splitType": "RWD_WALK", "noOfSplits": 13, "distance": 900.0,
                 "duration": 780.0, "averageHR": 130.0},
            ]},
        )
        rows = _extract_splits(detail)
        self.assertEqual(rows[0]["type"], "run")
        self.assertEqual(rows[0]["count"], 14)

    def test_interval_types_get_coaching_labels(self):
        from trellis.domain_move_service import _split_label
        self.assertEqual(_split_label("INTERVAL_ACTIVE"), "work")
        self.assertEqual(_split_label("INTERVAL_REST"), "recovery")
        self.assertEqual(_split_label("RWD_STAND"), "stand")

    def test_formatter_labels_aggregates_honestly(self):
        from trellis.domain_move_tool import _fmt_run_detail
        text = _fmt_run_detail({
            "overall": {"name": "Run-walk", "date": "2026-08-05"},
            "splits": [
                {"i": 1, "type": "run", "count": 14, "distance_km": 2.11,
                 "time": "25:00", "avg_hr": 152},
            ],
        })
        self.assertIn("Split totals by TYPE", text)
        self.assertIn("run ×14", text)
        self.assertNotIn("#1", text)


class TestAnnotateWorkout(unittest.TestCase):
    """Audit item 28 + logbook restructure: their account of ANY workout lands
    in user_note on the activity row — the column sync can't touch."""

    class _Repo:
        def __init__(self, workouts):
            self._w = {w.garmin_activity_id: w for w in workouts}

        def recent_workouts(self, user_id, *, limit):
            return list(self._w.values())

        def recent_runs(self, user_id, *, limit):
            return [w for w in self._w.values()
                    if "run" in (w.activity_type or "").lower()]

        def set_user_note(self, user_id, activity_id, note):
            from dataclasses import replace
            if activity_id in self._w:
                w = self._w[activity_id]
                base = w.note.split(" — ")[0]
                self._w[activity_id] = replace(
                    w, user_note=note, note=f"{base} — {note}")
                return True
            return False

        def get(self, user_id): return None
        def upsert(self, record): return record

    @staticmethod
    def _workout(day, name, kind="running", user_note=None, aid="a1"):
        from uuid import uuid4
        from trellis.domain_move_models import RunLog
        note = f"{name} — {user_note}" if user_note else name
        return RunLog(id=uuid4(), user_id=uuid4(), ran_on=day, note=note,
                      distance_km=6.41, garmin_activity_id=aid,
                      activity_type=kind, user_note=user_note)

    def _service(self, workouts):
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        class _Goals:
            def list_training_goals(self, uid): return []

        return MoveService(self._Repo(workouts), _Goals(), ZoneInfo("Europe/Madrid"))

    def test_annotation_lands_in_user_note(self):
        import datetime as dt
        w = self._workout(dt.date(2026, 8, 5), "Morning Running")
        svc = self._service([w])
        updated = svc.annotate_workout(w.user_id, dt.date(2026, 8, 5), "social run")
        self.assertEqual(updated.user_note, "social run")
        self.assertEqual(updated.note, "Morning Running — social run")

    def test_any_sport_annotatable(self):
        import datetime as dt
        w = self._workout(dt.date(2026, 8, 5), "Strength", kind="strength_training")
        svc = self._service([w])
        updated = svc.annotate_workout(w.user_id, dt.date(2026, 8, 5), "trainer destroyed my legs")
        self.assertEqual(updated.user_note, "trainer destroyed my legs")

    def test_two_activities_prefers_the_run(self):
        import datetime as dt
        d = dt.date(2026, 8, 5)
        strength = self._workout(d, "Strength", kind="strength_training", aid="s1")
        run = self._workout(d, "Morning Running", aid="r1")
        svc = self._service([strength, run])
        updated = svc.annotate_workout(run.user_id, d, "social run")
        self.assertEqual(updated.garmin_activity_id, "r1")

    def test_no_workout_that_date_returns_none(self):
        import datetime as dt
        from uuid import uuid4
        svc = self._service([])
        self.assertIsNone(svc.annotate_workout(uuid4(), dt.date(2026, 8, 5), "social run"))

    def test_duplicate_annotation_is_a_noop(self):
        import datetime as dt
        w = self._workout(dt.date(2026, 8, 5), "Morning Running", user_note="social run")
        svc = self._service([w])
        updated = svc.annotate_workout(w.user_id, dt.date(2026, 8, 5), "Social run")
        self.assertEqual(updated.user_note, "social run")


class TestPlanSaveMerges(unittest.TestCase):
    """12 Aug: an arc-note save wiped six days of stored week — the tool obeyed
    a wholesale replace. Saves now MERGE; only replace_week can drop days."""

    class _Repo:
        def __init__(self):
            self.stored = None

        def get(self, user_id):
            return self.stored

        def upsert(self, record):
            self.stored = record
            return record

        def add_run(self, run): return run
        def recent_runs(self, user_id, *, limit): return []
        def update_run_note(self, user_id, run_id, note): return False

    def _service(self):
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        class _Goals:
            def list_training_goals(self, uid): return []

        repo = self._Repo()
        return MoveService(repo, _Goals(), ZoneInfo("Europe/Madrid")), repo

    def _seed_week(self, svc, uid):
        week = [{"date": f"2026-08-{d:02d}", "type": "easy", "detail": f"day {d}"}
                for d in range(10, 17)]
        svc.save_plan(uid, plan={"arc": "the arc", "week": week}, replace_week=True)

    def test_arc_only_save_keeps_the_week(self):
        from uuid import uuid4
        svc, repo = self._service()
        uid = uuid4()
        self._seed_week(svc, uid)
        svc.save_plan(uid, plan={"arc": "the arc — with a strength note"})
        self.assertEqual(len(repo.stored.plan["week"]), 7)
        self.assertIn("strength note", repo.stored.plan["arc"])

    def test_one_day_merges_others_survive(self):
        from uuid import uuid4
        svc, repo = self._service()
        uid = uuid4()
        self._seed_week(svc, uid)
        svc.save_plan(uid, plan={"week": [
            {"date": "2026-08-12", "type": "easy", "detail": "UPDATED"}]})
        week = repo.stored.plan["week"]
        self.assertEqual(len(week), 7)
        updated = [s for s in week if s["date"] == "2026-08-12"][0]
        self.assertEqual(updated["detail"], "UPDATED")

    def test_replace_week_is_the_only_way_to_shrink(self):
        from uuid import uuid4
        svc, repo = self._service()
        uid = uuid4()
        self._seed_week(svc, uid)
        svc.save_plan(uid, plan={"week": [
            {"date": "2026-08-18", "type": "rest", "detail": "only day"}]},
            replace_week=True)
        self.assertEqual(len(repo.stored.plan["week"]), 1)


class TestPushReplaces(unittest.TestCase):
    """11 Aug: three Sunday re-pushes stacked four duplicates on one day.
    A push now deletes same-named workouts first."""

    class _Port:
        def __init__(self, existing):
            self.existing = existing
            self.deleted = []
            self.pushed = []
            self.scheduled = []

        def list_workouts(self, user_id, *, limit=30):
            return self.existing

        def delete_workout(self, user_id, workout_id):
            self.deleted.append(workout_id)

        def push_workout(self, user_id, workout_json):
            self.pushed.append(workout_json.get("workoutName"))
            return "new-id"

        def schedule_workout(self, user_id, workout_id, on_date):
            self.scheduled.append((workout_id, on_date))

    def test_same_named_workouts_deleted_before_push(self):
        import datetime as dt
        from uuid import uuid4
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        class _Goals:
            def list_training_goals(self, uid): return []

        class _Repo:
            def get(self, uid): return None
            def upsert(self, r): return r
            def recent_runs(self, uid, *, limit): return []
            def add_run(self, r): return r
            def update_run_note(self, uid, rid, note): return False

        port = self._Port(existing=[
            {"workoutId": "111", "workoutName": "Run-Walk-Run 4:1"},
            {"workoutId": "222", "workoutName": "Run-Walk-Run 4:1"},
            {"workoutId": "333", "workoutName": "Something Else"},
        ])
        svc = MoveService(_Repo(), _Goals(), ZoneInfo("Europe/Madrid"), garmin_push=port)
        svc.push_workout_to_watch(
            uuid4(),
            {"name": "Run-Walk-Run 4:1", "steps": [{"kind": "run", "duration": "40min"}]},
            dt.date(2026, 8, 12),
        )
        self.assertEqual(port.deleted, ["111", "222"])
        self.assertEqual(port.pushed, ["Run-Walk-Run 4:1"])
