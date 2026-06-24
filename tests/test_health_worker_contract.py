from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "services" / "health-worker"


class HealthWorkerContractTest(unittest.TestCase):
    def test_trellis_worker_runtime_files_exist(self):
        expected = {
            "app.py",
            "garmin.py",
            "Dockerfile",
            "requirements.txt",
        }

        self.assertTrue(expected.issubset({path.name for path in WORKER.iterdir()}))

    def test_app_exposes_client_contract_without_debug_endpoint(self):
        app_source = (WORKER / "app.py").read_text()
        tree = ast.parse(app_source)
        routes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", None)
            if attr not in {"get", "post"} or not node.args:
                continue
            route = node.args[0]
            if isinstance(route, ast.Constant):
                routes.add((attr, route.value))

        self.assertEqual(
            {
                ("get", "/health"),
                ("post", "/connect"),
                ("post", "/mfa"),
                ("post", "/sync"),
                ("post", "/activities"),
                ("post", "/activity-detail"),
                ("post", "/daily-health"),
            },
            routes,
        )
        self.assertIn("X-Worker-Secret", app_source)
        self.assertNotIn("/debug-activities", app_source)

    def test_worker_does_not_log_payloads_or_credentials(self):
        combined = "\n".join(
            [
                (WORKER / "app.py").read_text(),
                (WORKER / "garmin.py").read_text(),
            ]
        )

        forbidden_fragments = [
            "ACTIVITIES RESULT",
            "DAILY HEALTH RESULT",
            "for {email}",
            "for {req.email}",
            "session_dump=%",
            "password=%",
            "mfa_code=%",
        ]
        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, combined)
        self.assertIn("[redacted]", combined)

    def test_garmin_activity_shape_matches_trellis_client(self):
        module = _load_worker_garmin()

        normalized = module._normalize_activity(
            {
                "activityId": 123,
                "activityName": "Social Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": "2026-06-07 09:15:00.0",
                "duration": 3600,
                "calories": 500,
                "averageHeartRate": 151,
                "maxHeartRate": 176,
                "distance": 10000.0,
                "elevationGain": 20.0,
                "elevationLoss": 15.0,
                "activeSets": 4,
                "totalExerciseReps": 32,
                "summarizedExerciseSets": [{"category": "squat"}],
            }
        )

        self.assertEqual(
            {
                "activityId",
                "activityName",
                "activityType",
                "startTimeInSeconds",
                "duration",
                "calories",
                "avgHeartRate",
                "maxHeartRate",
                "distance",
                "elevationGain",
                "elevationLoss",
                "activeSets",
                "totalExerciseReps",
                "summarizedExerciseSets",
            },
            set(normalized),
        )
        self.assertEqual("running", normalized["activityType"])
        self.assertEqual(151, normalized["avgHeartRate"])

    def test_garmin_error_redaction(self):
        module = _load_worker_garmin()

        redacted = module._redact(
            "failed for cat@example.com with secret-password and worker-secret",
            "cat@example.com",
            "secret-password",
            "worker-secret",
        )

        self.assertNotIn("cat@example.com", redacted)
        self.assertNotIn("secret-password", redacted)
        self.assertNotIn("worker-secret", redacted)
        self.assertIn("[redacted]", redacted)

    def test_hrv_last_night_accepts_current_garmin_key(self):
        module = _load_worker_garmin()

        value = module._first_present(
            {
                "weeklyAvg": 52,
                "lastNightAvg": 47,
            },
            "lastNight",
            "lastNightAvg",
            "lastNightAverage",
        )

        self.assertEqual(47, value)


def _load_worker_garmin():
    module_path = WORKER / "garmin.py"
    spec = importlib.util.spec_from_file_location("trellis_health_worker_garmin", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load health worker garmin module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
