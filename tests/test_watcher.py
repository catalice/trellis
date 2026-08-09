"""The Watcher — verification engine, frame building, discovery parsing,
verdict handling. Pure Python, no DB, no API calls.

The core contract under test: silent below evidence (thin data verifies
nothing), deterministic verification (same frame, same verdict), her verdict
outranks the stats, dismissed never resurrects.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.core_watcher import (
    _parse_hypotheses,
    build_daily_frame,
    handle_pattern_response,
    verify,
)

TZ = ZoneInfo("Europe/Madrid")
D0 = date(2026, 6, 1)


def _frame(days: int, fill) -> dict:
    """Build a synthetic frame: fill(i) -> row dict for day i."""
    return {D0 + timedelta(days=i): fill(i) for i in range(days)}


class TestConditionCompare(unittest.TestCase):
    def test_verifies_a_real_luteal_effect(self):
        # 10 luteal days at mood 2, 10 other days at mood 4 — a real effect.
        frame = _frame(20, lambda i: {"mood": 2.0 if i < 10 else 4.0,
                                      "phase": "luteal" if i < 10 else "follicular"})
        verified, evidence, stats = verify(frame, {
            "type": "condition_compare", "metric": "mood", "condition": "phase:luteal",
        })
        self.assertTrue(verified)
        self.assertEqual(stats["n_with"], 10)
        self.assertIn("mood averages 2.0", evidence)

    def test_silent_below_evidence(self):
        # Only 3 luteal days — below the per-side minimum. Effect huge, still no.
        frame = _frame(20, lambda i: {"mood": 1.0 if i < 3 else 5.0,
                                      "phase": "luteal" if i < 3 else "follicular"})
        verified, evidence, _ = verify(frame, {
            "type": "condition_compare", "metric": "mood", "condition": "phase:luteal",
        })
        self.assertFalse(verified)
        self.assertIn("keep gathering", evidence)

    def test_small_effect_does_not_verify(self):
        frame = _frame(20, lambda i: {"mood": 3.2 if i < 10 else 3.4,
                                      "phase": "luteal" if i < 10 else "follicular"})
        verified, _, _ = verify(frame, {
            "type": "condition_compare", "metric": "mood", "condition": "phase:luteal",
        })
        self.assertFalse(verified)

    def test_ran_yesterday_condition(self):
        # Energy 4 the day after every run, 2 otherwise.
        frame = _frame(24, lambda i: {"energy": 4.0 if i % 2 else 2.0,
                                      "ran": i % 2 == 0})
        verified, _, stats = verify(frame, {
            "type": "condition_compare", "metric": "energy", "condition": "ran_yesterday",
        })
        self.assertTrue(verified)
        self.assertGreater(stats["mean_with"], stats["mean_without"])

    def test_unknown_test_type_reports_itself(self):
        verified, evidence, stats = verify({}, {"type": "seasonal_fourier"})
        self.assertFalse(verified)
        self.assertIn("can't verify", evidence)
        self.assertEqual(stats.get("error"), "unknown_test")


class TestCorrelation(unittest.TestCase):
    def test_verifies_strong_correlation(self):
        frame = _frame(15, lambda i: {"sleep_hours": 5 + (i % 4),
                                      "energy": 1 + (i % 4)})
        verified, evidence, stats = verify(frame, {
            "type": "correlation", "series_a": "sleep_hours", "series_b": "energy",
        })
        self.assertTrue(verified)
        self.assertGreaterEqual(stats["r"], 0.99)

    def test_lagged_correlation_shifts_the_pair(self):
        # sleep on day i predicts energy on day i+1, and ONLY lagged pairs align.
        frame = {}
        for i in range(15):
            frame[D0 + timedelta(days=i)] = {
                "sleep_hours": 5 + (i % 3),
                "energy": 1 + ((i - 1) % 3),
            }
        verified, _, stats = verify(frame, {
            "type": "correlation", "series_a": "sleep_hours", "series_b": "energy",
            "lag_days": 1,
        })
        self.assertTrue(verified)
        self.assertGreaterEqual(stats["r"], 0.99)

    def test_too_few_pairs_stays_silent(self):
        frame = _frame(5, lambda i: {"sleep_hours": 7, "energy": 3})
        verified, evidence, _ = verify(frame, {
            "type": "correlation", "series_a": "sleep_hours", "series_b": "energy",
        })
        self.assertFalse(verified)
        self.assertIn("keep gathering", evidence)


class TestDailyFrame(unittest.TestCase):
    def test_frame_assembles_all_sources(self):
        uid = uuid4()
        noon = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        states = [SimpleNamespace(felt_at=noon, energy=4, mood=3)]
        events = [
            SimpleNamespace(occurred_at=noon, event_type="sleep", value=7.5),
            SimpleNamespace(occurred_at=noon - timedelta(days=2),
                            event_type="period_start", value=None),
        ]
        health = [SimpleNamespace(observed_on=date(2026, 6, 3), sleep_score=82,
                                  sleep_duration_minutes=None, hrv_last_night=55.0,
                                  body_battery_end=71, body_battery_maximum=90,
                                  resting_heart_rate=54, average_stress=30)]
        runs = [SimpleNamespace(ran_on=date(2026, 6, 3))]
        frame = build_daily_frame(uid, states=states, events=events,
                                  health_rows=health, runs=runs, tz=TZ,
                                  today=date(2026, 6, 4))
        row = frame[date(2026, 6, 3)]
        self.assertEqual(row["energy"], 4)
        self.assertEqual(row["sleep_hours"], 7.5)
        self.assertEqual(row["sleep_score"], 82)
        self.assertEqual(row["hrv"], 55.0)
        self.assertTrue(row["ran"])
        self.assertEqual(row["cycle_day"], 3)
        self.assertEqual(row["phase"], "menstruation")


class TestDiscoveryParsing(unittest.TestCase):
    def test_parses_hypotheses_with_and_without_tests(self):
        raw = """```json
        {"hypotheses": [
          {"hypothesis": "Mood dips in the luteal phase",
           "test": {"type": "condition_compare", "metric": "mood", "condition": "phase:luteal"}},
          {"hypothesis": "Rough months follow short cycles", "test": null}
        ]}
        ```"""
        parsed = _parse_hypotheses(raw)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0][1]["metric"], "mood")
        self.assertIsNone(parsed[1][1])

    def test_garbage_yields_nothing(self):
        self.assertEqual(_parse_hypotheses("not json"), [])


class TestPatternResponse(unittest.TestCase):
    class _FakeWatcher:
        def __init__(self):
            self.calls = []

        def respond(self, user_id, pattern_id, verdict, note):
            self.calls.append((pattern_id, verdict, note))
            return {"hypothesis": "Mood dips in the luteal phase"}

    def test_dismissed_promises_never_again(self):
        w = self._FakeWatcher()
        reply = handle_pattern_response(
            uuid4(), {"pattern_id": str(uuid4()), "verdict": "dismissed"},
            datetime.now(timezone.utc), watcher=w,
        )
        self.assertIn("never come up again", reply)
        self.assertEqual(w.calls[0][1], "dismissed")

    def test_bad_verdict_rejected(self):
        reply = handle_pattern_response(
            uuid4(), {"pattern_id": str(uuid4()), "verdict": "maybe"},
            datetime.now(timezone.utc), watcher=self._FakeWatcher(),
        )
        self.assertIn("must be", reply)


if __name__ == "__main__":
    unittest.main()
