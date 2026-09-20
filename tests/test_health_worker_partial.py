"""The worker tells "Garmin has no reading" apart from "the request failed"."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

WORKER = Path(__file__).parent.parent / "services" / "health-worker" / "garmin.py"


def _load_worker():
    # The worker's own dependencies may not be installed in the app's
    # environment. Stand in for the missing ones only, and only while loading —
    # a stub left behind would shadow the real library for every later test.
    stubbed = []
    for name in ("garminconnect", "garth"):
        if importlib.util.find_spec(name) is None and name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
            stubbed.append(name)
    try:
        spec = importlib.util.spec_from_file_location("health_worker_garmin", WORKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)


class _Garmin:
    """Every request works except the ones named in `failing`."""
    def __init__(self, failing=()):
        self._failing = set(failing)

    def _call(self, name, value):
        if name in self._failing:
            raise RuntimeError("upstream timeout")
        return value

    def get_stats(self, d): return self._call("stats", {"totalSteps": 9000})
    def get_heart_rates(self, d): return self._call("heart_rate", {"restingHeartRate": 52})
    def get_sleep_data(self, d): return self._call("sleep", {"dailySleepDTO": {"sleepTimeSeconds": 25200}})
    def get_body_battery(self, a, b): return self._call("body_battery", [{"bodyBatteryValuesArray": [[0, 40], [1, 71]]}])
    def get_stress_data(self, d): return self._call("stress", {"avgStressLevel": 30})
    def get_hrv_data(self, d): return self._call("hrv", {"hrvSummary": {"lastNight": 55}})


def test_failed_requests_are_named_and_their_fields_left_out():
    worker = _load_worker()
    row = worker._fetch_daily_health_from_client(_Garmin(failing={"sleep", "hrv"}), "2026-03-10", include_date=True)
    assert row["unavailable"] == ["sleep", "hrv"]
    assert row["steps"] == 9000 and "sleep_duration_minutes" not in row and "hrv_last_night" not in row


def test_a_clean_fetch_carries_no_mark():
    worker = _load_worker()
    row = worker._fetch_daily_health_from_client(_Garmin(), "2026-03-10", include_date=True)
    assert "unavailable" not in row and row["sleep_duration_minutes"] == 420
