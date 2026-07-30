"""
Deterministic tests for the Garmin structured-workout builder and CSV baseline.
No network — run offline with:  uv run pytest tests/test_training_workout.py -q
"""
from __future__ import annotations

import unittest

from trellis.domain_training_service import (
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
        self.assertEqual(rpt["endConditionValue"], 6)
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
        self.assertIsNone(step["endConditionValue"])

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
