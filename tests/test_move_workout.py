"""
Deterministic tests for the Garmin structured-workout builder and CSV baseline.
No network — run offline with:  uv run pytest tests/test_move_workout.py -q
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


class TestActivityVisibility(unittest.TestCase):
    """The coach must SEE every activity type (the Friday-strength bug: the old
    runs-only reader made 'my most recent workout' answer with an older run).
    The run LOG stays runs-only — it feeds the running baseline."""

    def _service(self, activities):
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        from datetime import datetime, timezone as tzu
        from uuid import uuid4
        from trellis.domain_move_models import RunLog

        def view(act):
            return RunLog(
                id=uuid4(), user_id=uuid4(),
                ran_on=datetime.fromtimestamp(
                    act.start_time_epoch_seconds, tz=tzu.utc).date(),
                note=act.name, name=act.name,
                distance_km=(round(act.distance_meters / 1000, 2)
                             if act.distance_meters else None),
                garmin_activity_id=act.activity_id,
                activity_type=act.activity_type,
                duration_min=round(act.duration_milliseconds / 60000, 1),
                avg_hr=act.average_heart_rate, max_hr=act.maximum_heart_rate,
            )

        class FakeRepo:
            def recent_workouts(self, user_id, *, limit):
                return [view(a) for a in activities[:limit]]
            def recent_runs(self, user_id, limit=200):
                return []

        self.repo = FakeRepo()
        return MoveService(self.repo, goals=None, tz=ZoneInfo("Europe/Madrid"))

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

    def test_two_activities_and_no_sport_named_is_refused_not_guessed(self):
        """It used to prefer the run, else take the first — and once filed the
        account of a run on a strength session, then said it had logged the run."""
        import datetime as dt
        from trellis.domain_move_service import AmbiguousWorkout
        d = dt.date(2026, 8, 5)
        strength = self._workout(d, "Strength", kind="strength_training", aid="s1")
        run = self._workout(d, "Morning Running", aid="r1")
        svc = self._service([strength, run])
        with self.assertRaises(AmbiguousWorkout) as caught:
            svc.annotate_workout(run.user_id, d, "social run")
        self.assertEqual({w.garmin_activity_id for w in caught.exception.candidates}, {"s1", "r1"})
        self.assertIsNone(strength.user_note)                       # nothing was written anywhere

    def test_naming_the_sport_picks_that_activity(self):
        import datetime as dt
        d = dt.date(2026, 8, 5)
        strength = self._workout(d, "Strength", kind="strength_training", aid="s1")
        run = self._workout(d, "Morning Running", aid="r1")
        svc = self._service([strength, run])
        self.assertEqual(svc.annotate_workout(run.user_id, d, "social run", sport="run").garmin_activity_id, "r1")
        self.assertEqual(svc.annotate_workout(run.user_id, d, "heavy legs", sport="strength").garmin_activity_id, "s1")

    def test_a_run_that_has_not_synced_yet_is_not_filed_on_the_strength_session(self):
        """The 16 Sep failure: the run wasn't on record yet, only the strength session was."""
        import datetime as dt
        from trellis.domain_move_service import NoSuchWorkout
        d = dt.date(2026, 8, 5)
        strength = self._workout(d, "Strength", kind="strength_training", aid="s1")
        svc = self._service([strength])
        with self.assertRaises(NoSuchWorkout) as caught:
            svc.annotate_workout(strength.user_id, d, "easy 5k, felt good", sport="run")
        self.assertEqual([w.garmin_activity_id for w in caught.exception.that_day], ["s1"])

    def test_a_note_filed_on_the_wrong_activity_can_be_taken_off(self):
        import datetime as dt
        d = dt.date(2026, 8, 5)
        strength = self._workout(d, "Strength", kind="strength_training", aid="s1",
                                 user_note="heavy legs — easy 5k, felt good")
        svc = self._service([strength])
        updated = svc.annotate_workout(strength.user_id, d, "", sport="strength", remove="easy 5k, felt good")
        self.assertEqual(updated.user_note, "heavy legs")

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
    """A pushed workout is a DATED SESSION, identified by what Trellis itself
    pushed for that date — never by its name. (The first version deleted every
    same-named workout before uploading: pushing Wednesday's "Easy Run" removed
    Monday's, and a failed upload had already deleted the old one.)"""

    class _Port:
        def __init__(self, fail_push=False, fail_schedule=False, fail_delete=False):
            self.library = {}                 # workout_id -> name
            self.scheduled = {}               # workout_id -> date
            self.deleted = []
            self._n = 0
            self.fail_push, self.fail_schedule, self.fail_delete = fail_push, fail_schedule, fail_delete

        def list_workouts(self, user_id, *, limit=30):
            return [{"workoutId": k, "workoutName": v} for k, v in self.library.items()]

        def delete_workout(self, user_id, workout_id):
            if self.fail_delete:
                raise ConnectionError("garmin down")
            self.deleted.append(workout_id)
            self.library.pop(workout_id, None)
            self.scheduled.pop(workout_id, None)

        def push_workout(self, user_id, workout_json):
            if self.fail_push:
                raise ConnectionError("garmin down")
            self._n += 1
            self.library[f"w{self._n}"] = workout_json.get("workoutName")
            return f"w{self._n}"

        def schedule_workout(self, user_id, workout_id, on_date):
            if self.fail_schedule:
                raise ConnectionError("garmin down")
            self.scheduled[workout_id] = on_date

    class _Repo:
        def __init__(self): self.pushes = {}
        def get(self, uid): return None
        def upsert(self, r): return r
        def recent_runs(self, uid, *, limit): return []
        def get_watch_push(self, uid, on_date, name): return self.pushes.get((on_date, name))
        def record_watch_push(self, uid, on_date, name, workout_id): self.pushes[(on_date, name)] = workout_id

    EASY = {"name": "Easy Run", "steps": [{"kind": "run", "duration": "40min"}]}

    def _svc(self, port, repo=None):
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService

        class _Goals:
            def list_training_goals(self, uid): return []
        return MoveService(repo or self._Repo(), _Goals(), ZoneInfo("Europe/Madrid"), garmin_push=port)

    def test_two_days_with_the_same_name_both_stay_on_the_watch(self):
        import datetime as dt
        port, uid = self._Port(), uuid.uuid4()
        svc = self._svc(port)
        svc.push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 9))       # Monday
        svc.push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))      # Wednesday, same name
        self.assertEqual(port.deleted, [])
        self.assertEqual(sorted(port.scheduled.values()), [dt.date(2026, 3, 9), dt.date(2026, 3, 11)])

    def test_a_repush_for_the_same_day_replaces_only_that_days_workout(self):
        import datetime as dt
        port, uid = self._Port(), uuid.uuid4()
        svc = self._svc(port)
        svc.push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 9))
        svc.push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        result = svc.push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))   # a correction
        self.assertTrue(result.replaced and not result.old_copy_left)
        self.assertEqual(port.deleted, ["w2"])                                      # Wednesday's old one only
        self.assertEqual(port.scheduled, {"w1": dt.date(2026, 3, 9), "w3": dt.date(2026, 3, 11)})

    def test_a_failed_upload_leaves_the_previous_workout_in_place(self):
        import datetime as dt
        port, repo, uid = self._Port(), self._Repo(), uuid.uuid4()
        self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        port.fail_push = True
        with self.assertRaises(ConnectionError):
            self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        self.assertEqual(port.deleted, [])
        self.assertEqual(port.scheduled, {"w1": dt.date(2026, 3, 11)})
        self.assertEqual(repo.pushes[(dt.date(2026, 3, 11), "Easy Run")], "w1")

    def test_an_upload_that_cannot_be_scheduled_is_taken_back_and_the_old_one_kept(self):
        import datetime as dt
        port, repo, uid = self._Port(), self._Repo(), uuid.uuid4()
        self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        port.fail_schedule = True
        with self.assertRaises(ConnectionError):
            self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        self.assertEqual(port.deleted, ["w2"])                          # only the unscheduled newcomer
        self.assertEqual(list(port.library), ["w1"])
        self.assertEqual(repo.pushes[(dt.date(2026, 3, 11), "Easy Run")], "w1")

    def test_an_old_copy_that_cannot_be_removed_is_reported_not_hidden(self):
        import datetime as dt
        port, repo, uid = self._Port(), self._Repo(), uuid.uuid4()
        self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        port.fail_delete = True
        result = self._svc(port, repo).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        self.assertTrue(result.old_copy_left)
        self.assertEqual(repo.pushes[(dt.date(2026, 3, 11), "Easy Run")], "w2")     # the new one is the record

    def test_a_workout_trellis_did_not_push_is_never_deleted_by_name(self):
        import datetime as dt
        port, uid = self._Port(), uuid.uuid4()
        port.library["theirs"] = "Easy Run"                             # made by hand in Garmin
        self._svc(port).push_workout_to_watch(uid, self.EASY, dt.date(2026, 3, 11))
        self.assertIn("theirs", port.library)


class TestStructuredSplitFilter(unittest.TestCase):
    """30 Aug: a 5-interval VO2max read as '~7 rounds' because Garmin's
    run-walk auto-detection buried the 12 real INTERVAL_* segments in 65
    RWD_* micro-segments. Structured workouts are described by their
    structure alone; counting is Python's job."""

    class _Detail:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def test_interval_segments_win_over_rwd_noise(self):
        from trellis.domain_move_service import _extract_splits
        noise = [{"type": t, "distance": 50.0, "duration": 30.0}
                 for t in ["RWD_RUN", "RWD_WALK", "RWD_STAND"] * 10]
        structure = (
            [{"type": "INTERVAL_WARMUP", "distance": 1000.0, "duration": 600.0}]
            + [{"type": "INTERVAL_ACTIVE", "distance": 600.0, "duration": 180.0,
                "averageHR": 175.0},
               {"type": "INTERVAL_RECOVERY", "distance": 200.0, "duration": 120.0}] * 5
            + [{"type": "INTERVAL_COOLDOWN", "distance": 800.0, "duration": 480.0}]
        )
        detail = self._Detail(
            splits=[], split_summaries={},
            typed_splits={"splits": noise[:15] + structure + noise[15:]},
        )
        rows = _extract_splits(detail)
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(1 for r in rows if r["type"] == "work"), 5)

    def test_rwd_only_sessions_unaffected(self):
        from trellis.domain_move_service import _extract_splits
        detail = self._Detail(
            splits=[], split_summaries={},
            typed_splits={"splits": [
                {"type": "RWD_RUN", "distance": 640.0, "duration": 252.0},
                {"type": "RWD_WALK", "distance": 90.0, "duration": 61.0},
            ]},
        )
        rows = _extract_splits(detail)
        self.assertEqual(len(rows), 2)


class TestSteadyRunReadsLaps(unittest.TestCase):
    """15 Sep: 'it says my HR was X overall but I was walking'. Two causes —
    plain laps carry their label in intensityType (unread: every lap reached
    the coach untyped), and a single-step workout's structured view is ONE
    30-minute 'work' row, hiding the per-km story. Now: laps are labelled, a
    single-effort structure yields to the km laps, and Python states the
    running portion (warm-up/cool-down excluded) as a fact."""

    class _Detail:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def _easy_run(self):
        laps = [
            {"intensityType": "WARMUP", "distance": 5.0, "duration": 3.0, "averageHR": 83.0},
            {"intensityType": "ACTIVE", "distance": 1000.0, "duration": 490.0, "averageHR": 141.0},
            {"intensityType": "ACTIVE", "distance": 1000.0, "duration": 456.0, "averageHR": 160.0},
            {"intensityType": "ACTIVE", "distance": 1000.0, "duration": 495.0, "averageHR": 155.0},
            {"intensityType": "ACTIVE", "distance": 733.0, "duration": 359.0, "averageHR": 158.0},
            {"intensityType": "COOLDOWN", "distance": 2.0, "duration": 1.0, "averageHR": 159.0},
        ]
        return self._Detail(
            splits={"lapDTOs": laps}, split_summaries={},
            typed_splits={"splits": [
                {"type": "INTERVAL_WARMUP", "distance": 5.0, "duration": 3.0, "averageHR": 83.0},
                {"type": "INTERVAL_ACTIVE", "distance": 3733.0, "duration": 1800.0, "averageHR": 153.0},
            ]},
        )

    def test_lap_intensity_type_is_read(self):
        from trellis.domain_move_service import _extract_splits
        rows = _extract_splits(self._Detail(
            splits={"lapDTOs": [
                {"intensityType": "WARMUP", "distance": 400.0, "duration": 240.0},
                {"intensityType": "ACTIVE", "distance": 1000.0, "duration": 480.0},
                {"intensityType": "COOLDOWN", "distance": 300.0, "duration": 200.0},
            ]}, split_summaries={}, typed_splits={},
        ))
        self.assertEqual([r["type"] for r in rows], ["warmup", "run", "cooldown"])

    def test_single_effort_structure_yields_to_km_laps(self):
        from trellis.domain_move_service import _extract_splits
        rows = _extract_splits(self._easy_run())
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["avg_hr"] for r in rows if r["type"] == "run"], [141, 160, 155, 158])

    def test_multi_effort_structure_still_wins(self):
        from trellis.domain_move_service import _extract_splits
        detail = self._Detail(
            splits={"lapDTOs": [{"intensityType": "ACTIVE", "distance": 1000.0, "duration": 400.0}] * 8},
            split_summaries={},
            typed_splits={"splits": [
                {"type": "INTERVAL_ACTIVE", "distance": 500.0, "duration": 180.0},
                {"type": "INTERVAL_RECOVERY", "distance": 200.0, "duration": 120.0},
            ] * 4},
        )
        rows = _extract_splits(detail)
        self.assertEqual(sum(1 for r in rows if r["type"] == "work"), 4)

    def test_running_portion_excludes_the_walks(self):
        from trellis.domain_move_service import _extract_splits, running_portion
        running = running_portion(_extract_splits(self._easy_run()))
        self.assertEqual(running["segments"], 4)
        self.assertEqual(running["distance_km"], 3.73)
        self.assertEqual(running["time"], "30:00")
        self.assertEqual(running["avg_hr"], 153)   # duration-weighted, walks out

    def test_running_portion_is_none_without_labels(self):
        from trellis.domain_move_service import running_portion
        self.assertIsNone(running_portion([{"i": 1, "type": "run", "_secs": 300.0, "avg_hr": 150}]))

    def test_run_detail_states_the_running_portion(self):
        from trellis.domain_move_tool import _fmt_run_detail
        text = _fmt_run_detail({
            "overall": {"name": "Easy run", "avg_hr": 152},
            "splits": [{"i": 1, "type": "run", "time": "30:00", "avg_hr": 153}],
            "running": {"segments": 4, "time": "30:00", "distance_km": 3.73, "avg_hr": 153},
        })
        self.assertIn("Running portion (warm-up/cool-down excluded): 30:00, 3.73km, avg HR 153", text)
        self.assertIn("not the overall average", text)


class TestSyncReportsReadiness(unittest.TestCase):
    """15 Sep: sync_garmin said 'done' and the model kept quoting the pre-sync
    body battery (17, when 71 had just landed). The fresh numbers ride the receipt."""

    def test_sync_receipt_carries_fresh_readiness(self):
        from datetime import datetime, timezone
        from trellis.domain_move_tool import handle_sync_garmin

        class Move:
            def sync_garmin(self, uid, *, now):
                return {"activities": 1, "health_records": 3, "health_through": "2026-09-15"}

        class Sense:
            def recent_health(self, uid, now=None):
                return {"date": "2026-09-15", "stale_days": 0, "synced_at": "07:17",
                        "body_battery_end": 71, "body_battery_high": 71, "resting_hr": 49}

        out = handle_sync_garmin(uuid.uuid4(), {}, datetime.now(timezone.utc),
                                 move_service=Move(), sense_service=Sense())
        self.assertIn("Synced Garmin — 1 activity refreshed, health up to 2026-09-15 (3 day(s)).", out)
        self.assertIn("Readiness now:", out)
        self.assertIn("body battery 71", out)

    def test_sync_without_sense_is_unchanged(self):
        from datetime import datetime, timezone
        from trellis.domain_move_tool import handle_sync_garmin

        class Move:
            def sync_garmin(self, uid, *, now):
                return {"activities": 0}

        out = handle_sync_garmin(uuid.uuid4(), {}, datetime.now(timezone.utc), move_service=Move())
        self.assertEqual(out, "Synced Garmin — 0 activities refreshed.")


if __name__ == "__main__":
    unittest.main()


class TestMoveUpdateFold(unittest.TestCase):
    """15 Sep 2026: save_training_plan + update_workout folded into one write
    door, move_update(what=plan|baseline|workout). The dispatcher must reach the
    proven handlers and a bare baseline write must not touch the stored week."""

    def _service(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService
        from trellis.domain_move_models import RunLog, TrainingPlan
        from uuid import uuid4

        w = RunLog(id=uuid4(), user_id=uuid4(), ran_on=dt.date(2026, 9, 14),
                   note="Morning Running", distance_km=5.0, garmin_activity_id="a1",
                   activity_type="running", user_note=None)

        class _Repo:
            def __init__(self):
                self.plan = None
                self.workouts = [w]
            def get(self, user_id): return self.plan
            def open_proposal(self, user_id): return None
            def upsert(self, record):
                self.plan = record
                return record
            def recent_workouts(self, user_id, *, limit): return self.workouts
            def recent_runs(self, user_id, *, limit): return self.workouts
            def set_user_note(self, user_id, activity_id, note):
                import dataclasses
                for i, x in enumerate(self.workouts):
                    if x.garmin_activity_id == activity_id:
                        self.workouts[i] = dataclasses.replace(x, user_note=note, note=f"{x.note} — {note}")
                        return True
                return False

        class _Goals:
            def list_training_goals(self, uid): return []

        self.repo = _Repo()
        self.user = w.user_id
        return MoveService(self.repo, _Goals(), ZoneInfo("Europe/Madrid"))

    def test_plan_then_baseline_keeps_week(self):
        from datetime import datetime, timezone
        from trellis.domain_move_tool import handle_move_update
        svc = self._service()
        now = datetime.now(timezone.utc)
        # The week gets stored by an approved proposal (tests/test_plan_agreement.py);
        # here it is seeded, because this test is about what a baseline write keeps.
        svc.save_plan(self.user, plan={"arc": "base", "week": [{"date": "2026-09-15", "type": "easy", "detail": "5k"}]})
        out = handle_move_update(self.user, {"what": "baseline", "baseline": "Z2 ~7:00/km"},
                                 now, move_service=svc)
        self.assertEqual(out, "Baseline stored.")
        self.assertEqual(self.repo.plan.baseline, "Z2 ~7:00/km")
        self.assertEqual(len(self.repo.plan.plan["week"]), 1)

    def test_workout_note_appends(self):
        from datetime import datetime, timezone
        from trellis.domain_move_tool import handle_move_update
        svc = self._service()
        out = handle_move_update(self.user, {"what": "workout", "date": "2026-09-14",
                                             "note": "social run"},
                                 datetime.now(timezone.utc), move_service=svc)
        self.assertIn("social run", out)

    def test_unknown_what(self):
        from datetime import datetime, timezone
        from trellis.domain_move_tool import handle_move_update
        out = handle_move_update(self.user if hasattr(self, "user") else uuid.uuid4(),
                                 {"what": "race"}, datetime.now(timezone.utc),
                                 move_service=self._service())
        self.assertIn("plan, baseline, or workout", out)


from datetime import datetime as _dt, timezone as _tz

_NOON = _dt(2026, 3, 10, 12, 0, tzinfo=_tz.utc)


class TestPartialSyncReceipt:
    """A sync where a Garmin request failed is reported as partial, naming what
    wasn't refreshed — never a plain 'Synced'."""

    class _Move:
        def __init__(self, unavailable):
            self._unavailable = unavailable

        def sync_garmin(self, user_id, *, now):
            return {"activities": 1, "health_records": 3, "health_through": "2026-03-10",
                    "unavailable": self._unavailable}

    def test_partial_names_the_day_and_the_groups(self):
        from trellis.domain_move_tool import handle_sync_garmin
        out = handle_sync_garmin(uuid.uuid4(), {}, _NOON,
                                 move_service=self._Move({"2026-03-10": ("sleep", "hrv")}))
        assert out.startswith("Partly synced Garmin")
        assert "2026-03-10: sleep, hrv" in out

    def test_complete_sync_reads_as_before(self):
        from trellis.domain_move_tool import handle_sync_garmin
        out = handle_sync_garmin(uuid.uuid4(), {}, _NOON,
                                 move_service=self._Move({}))
        assert out.startswith("Synced Garmin") and "Not refreshed" not in out


class TestWholeSessionContainer:
    """A container row duplicates the whole session and must go; a long real
    effort must never be mistaken for one."""

    @staticmethod
    def _detail(rows):
        from trellis.infra_garmin import GarminActivityDetail
        return GarminActivityDetail(activity_id="x", raw={"typedSplits": {"splits": rows}})

    def test_a_dominant_real_effort_is_kept(self):
        from trellis.domain_move_service import _extract_splits
        rows = [{"type": "RWD_WALK", "duration": 300.0, "distance": 400.0},
                {"type": "RWD_RUN", "duration": 1800.0, "distance": 5000.0},
                {"type": "RWD_STAND", "duration": 120.0, "distance": 5.0},
                {"type": "RWD_WALK", "duration": 300.0, "distance": 400.0}]
        assert [s["time"] for s in _extract_splits(self._detail(rows))] == ["5:00", "30:00", "2:00", "5:00"]

    def test_a_row_enclosing_the_others_in_time_is_dropped(self):
        from trellis.domain_move_service import _extract_splits
        def row(kind, start, secs):
            h, m = divmod(start // 60, 60)
            eh, em = divmod((start + secs) // 60, 60)
            return {"type": kind, "duration": float(secs), "distance": 100.0,
                    "startTimeGMT": f"2026-03-10T{8 + h:02d}:{m:02d}:00.0",
                    "endTimeGMT": f"2026-03-10T{8 + eh:02d}:{em:02d}:00.0"}
        rows = [row("RWD_RUN", 0, 2520), row("RWD_WALK", 0, 300), row("RWD_RUN", 300, 1800),
                row("RWD_STAND", 2100, 120), row("RWD_WALK", 2220, 300)]
        assert [s["time"] for s in _extract_splits(self._detail(rows))] == ["5:00", "30:00", "2:00", "5:00"]

    def test_without_times_nothing_is_dropped_on_duration(self):
        """5 + 15 + 5 + 5: the 15 equals the rest combined and is the actual run.
        Duration is never evidence of a container."""
        from trellis.domain_move_service import _extract_splits
        rows = [{"type": "RWD_WALK", "duration": 300.0, "distance": 400.0},
                {"type": "RWD_RUN", "duration": 900.0, "distance": 2500.0},
                {"type": "RWD_STAND", "duration": 300.0, "distance": 5.0},
                {"type": "RWD_WALK", "duration": 300.0, "distance": 400.0}]
        assert [s["time"] for s in _extract_splits(self._detail(rows))] == ["5:00", "15:00", "5:00", "5:00"]


class TestFirstReviewOfAnActivitySaysWhatFailed:
    """The first review fetches from Garmin and stores. A section that failed in
    that fetch must be named there and then — not only on a later, cached read."""

    def test_a_failed_lap_fetch_is_named_on_the_first_review(self):
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from trellis.domain_move_service import MoveService
        from trellis.domain_move_tool import _fmt_run_detail
        from trellis.infra_garmin import GarminActivityDetail

        act = SimpleNamespace(garmin_activity_id="g1", name="Morning Run", ran_on=_NOON.date(),
                              distance_km=5.0, duration_min=30.0, avg_hr=150, max_hr=165,
                              note=None, user_note=None, activity_type="running")

        class Health:
            stored = None
            def get_activity_detail(self, uid, aid):
                return None                                  # never fetched before
            def upsert_activity_detail(self, *, user_id, activity_id, raw_data, sync_run_id):
                Health.stored = raw_data
                return ("splits",)                           # the repository's normalised verdict

        class Reader:
            def activity_detail(self, uid, aid):
                return GarminActivityDetail(activity_id=aid, raw={"splitsError": "upstream timeout"})

        svc = MoveService.__new__(MoveService)
        svc._health, svc._garmin_read = Health(), Reader()
        svc._repo = SimpleNamespace(recent_workouts=lambda uid, limit: [act])
        detail = svc.review_run(uuid.uuid4())
        assert detail["not_fetched"] == ["splits"]
        assert "Not fetched from Garmin" in _fmt_run_detail(detail)
