"""The Watcher — verification engine, frame building, discovery parsing,
verdict handling. Pure Python, no DB, no API calls.

The core contract under test: silent below evidence (thin data verifies
nothing), deterministic verification (same frame, same verdict), their verdict
outranks the stats, dismissed never resurrects.
"""
from __future__ import annotations

import re
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
            "type": "condition_compare", "metric": "mood", "condition": "phase:luteal", "expect": "lower",
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
                                      "ran": i % 2 == 0, "watch": True})
        verified, _, stats = verify(frame, {
            "type": "condition_compare", "metric": "energy", "condition": "ran_yesterday", "expect": "higher",
        })
        self.assertTrue(verified)
        self.assertGreater(stats["mean_with"], stats["mean_without"])

    def test_unknown_test_type_reports_itself(self):
        verified, evidence, stats = verify({}, {"type": "seasonal_fourier"})
        self.assertFalse(verified)
        self.assertIn("can't verify", evidence)
        self.assertEqual(stats.get("error"), "unknown_test")


class TestAHypothesisCanBeWrong(unittest.TestCase):
    """A difference in EITHER direction used to verify: 'mood is better on meds
    days' was confirmed by mood being worse on them."""

    def _meds_days_are_worse(self):
        return _frame(20, lambda i: {"mood": 2.0 if i < 10 else 4.0, "logged": True,
                                     "meds": i < 10})

    def test_the_opposite_result_refutes_it(self):
        verified, evidence, stats = verify(self._meds_days_are_worse(), {
            "type": "condition_compare", "metric": "mood", "condition": "meds_logged", "expect": "higher",
        })
        self.assertFalse(verified)
        self.assertIn("OPPOSITE", evidence)
        self.assertIn("mood averages 2.0", evidence)          # the numbers are still shown
        self.assertIn("not_confirmed", stats)

    def test_the_same_data_confirms_the_hypothesis_it_actually_supports(self):
        verified, _, _ = verify(self._meds_days_are_worse(), {
            "type": "condition_compare", "metric": "mood", "condition": "meds_logged", "expect": "lower",
        })
        self.assertTrue(verified)

    def test_no_stated_direction_confirms_nothing(self):
        verified, evidence, _ = verify(self._meds_days_are_worse(), {
            "type": "condition_compare", "metric": "mood", "condition": "meds_logged",
        })
        self.assertFalse(verified)
        self.assertIn("names no direction", evidence)

    def test_a_correlation_the_wrong_way_round_refutes_it(self):
        frame = _frame(15, lambda i: {"sleep_hours": 5 + (i % 4), "energy": 5 - (i % 4)})
        verified, evidence, _ = verify(frame, {
            "type": "correlation", "series_a": "sleep_hours", "series_b": "energy", "expect": "positive",
        })
        self.assertFalse(verified)
        self.assertIn("OPPOSITE", evidence)

    def test_not_confirmed_is_a_result_not_an_error(self):
        """It must be STORED: `error` in stats means the test couldn't run and
        the old evidence is kept — a refutation has to replace it."""
        _, _, stats = verify(self._meds_days_are_worse(), {
            "type": "condition_compare", "metric": "mood", "condition": "meds_logged", "expect": "higher",
        })
        self.assertNotIn("error", stats)


class TestMissingIsUnknown(unittest.TestCase):
    """A day with nothing recorded is not a day without a run or a medication."""

    def test_days_with_no_logging_are_left_out_of_a_meds_comparison(self):
        # 6 meds days at mood 4; 6 logged days without at mood 2; 8 days where
        # mood came from elsewhere and NOTHING was logged — unknown, not 'no meds'.
        def fill(i):
            if i < 6:
                return {"mood": 4.0, "logged": True, "meds": True}
            if i < 12:
                return {"mood": 2.0, "logged": True}
            return {"mood": 4.0}
        verified, evidence, stats = verify(_frame(20, fill), {
            "type": "condition_compare", "metric": "mood", "condition": "meds_logged", "expect": "higher",
        })
        self.assertTrue(verified)
        self.assertEqual((stats["n_with"], stats["n_without"]), (6, 6))
        self.assertIn("on other logged days", evidence)       # says what the other side really is

    def test_a_day_the_watch_recorded_nothing_is_not_a_rest_day(self):
        frame = _frame(20, lambda i: {"energy": 3.0, "ran": True, "watch": True} if i < 6 else {"energy": 3.0})
        verified, evidence, stats = verify(frame, {
            "type": "condition_compare", "metric": "energy", "condition": "ran_today", "expect": "higher",
        })
        self.assertFalse(verified)
        self.assertEqual(stats["n_without"], 0)
        self.assertIn("keep gathering", evidence)


class TestMedicationIdentity(unittest.TestCase):
    """One boolean made every medication the same medication."""

    def _events(self):
        at = lambda i: datetime(2026, 6, 1 + i, 9, 0, tzinfo=TZ)
        meds = lambda i, name: SimpleNamespace(event_type="meds", detail=name, value=None, occurred_at=at(i))
        return [meds(0, "Ibuprofen 400mg"), meds(0, "antihistamine"), meds(1, "antihistamine"), meds(2, "meds")]

    def test_the_frame_keeps_which_medication(self):
        frame = build_daily_frame(uuid4(), states=[], events=self._events(), health_rows=[], runs=[],
                                  tz=TZ, today=date(2026, 6, 10))
        self.assertEqual(frame[date(2026, 6, 1)]["meds_names"], ("antihistamine", "ibuprofen 400mg"))
        self.assertEqual(frame[date(2026, 6, 2)]["meds_names"], ("antihistamine",))
        self.assertNotIn("meds_names", frame[date(2026, 6, 3)])      # logged, unnamed: still a meds day
        self.assertTrue(frame[date(2026, 6, 3)]["meds"])

    def test_a_name_survives_the_trip_through_discovery_and_back(self):
        """Discovery was shown 'example_medicine_10mg' and told to use the name as
        it appears; verification matched 'example medicine 10mg' — zero days."""
        from trellis.core_watcher import Watcher
        at = lambda i: datetime(2026, 6, 1 + i, 9, 0, tzinfo=TZ)
        events = [SimpleNamespace(event_type="meds", detail="Example Medicine 10mg", value=None, occurred_at=at(i))
                  for i in range(6)]
        states = [SimpleNamespace(felt_at=at(i), energy=None, mood=4 if i < 6 else 2, note="", extra={}) for i in range(12)]
        frame = build_daily_frame(uuid4(), states=states, events=events, health_rows=[], runs=[],
                                  tz=TZ, today=date(2026, 6, 20))
        watcher = Watcher(None, None, state_repo=None, health_repo=None, run_repo=None, tz=TZ)
        shown = re.search(r"meds=\[([^\]]+)\]", watcher._daily_lines(frame)).group(1)      # what discovery reads
        for spelling in (shown, "example_medicine_10mg", "Example Medicine 10mg"):
            verified, _, stats = verify(frame, {"type": "condition_compare", "metric": "mood",
                                                "condition": f"meds:{spelling}", "expect": "higher"})
            self.assertEqual(stats["n_with"], 6, spelling)
            self.assertTrue(verified, spelling)

    def test_a_named_condition_means_that_medication_only(self):
        from trellis.core_watcher import _condition_holds
        day = {"logged": True, "meds": True, "meds_names": ("ibuprofen 400mg",)}
        self.assertTrue(_condition_holds(day, None, "meds:ibuprofen"))           # any dose of it
        self.assertTrue(_condition_holds(day, None, "meds:ibuprofen 400mg"))
        self.assertFalse(_condition_holds(day, None, "meds:antihistamine"))      # logged, but not this one
        self.assertIsNone(_condition_holds({}, None, "meds:antihistamine"))      # nothing logged: unknown


class TestAnAdoptedPatternNeverRidesAlone(unittest.TestCase):
    def test_context_carries_what_the_data_says_now(self):
        """Adopted, the sentence alone went into every turn — even after the
        data stopped agreeing with it."""
        from trellis.core_watcher import Watcher
        repo = SimpleNamespace(all_for=lambda uid: [{
            "id": uuid4(), "status": "adopted", "hypothesis": "Mood is higher on days meds were logged.",
            "evidence": "mood averages 2.0 on days meds were logged vs 4.0 on other logged days "
                        "(10 vs 10 days) — the OPPOSITE of the proposed 'higher'",
        }])
        watcher = Watcher(repo, None, state_repo=None, health_repo=None, run_repo=None, tz=TZ)
        context = watcher.intelligence_context(uuid4(), datetime(2026, 6, 20, tzinfo=TZ))
        self.assertIn("latest data: mood averages 2.0", context)
        self.assertIn("OPPOSITE", context)
        self.assertIn("not causes", context)


class TestCorrelation(unittest.TestCase):
    def test_verifies_strong_correlation(self):
        frame = _frame(15, lambda i: {"sleep_hours": 5 + (i % 4),
                                      "energy": 1 + (i % 4)})
        verified, evidence, stats = verify(frame, {
            "type": "correlation", "series_a": "sleep_hours", "series_b": "energy", "expect": "positive",
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
            "lag_days": 1, "expect": "positive",
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


class TestNotesInFrame(unittest.TestCase):
    def test_her_words_land_truncated_and_capped(self):
        uid = uuid4()
        noon = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        states = [SimpleNamespace(felt_at=noon, energy=2, mood=2,
                                  note="feeling really anxious about " + "x" * 100)]
        states += [SimpleNamespace(felt_at=noon, energy=None, mood=None,
                                   note=f"note {i}") for i in range(5)]
        frame = build_daily_frame(uid, states=states, events=[], health_rows=[],
                                  runs=[], tz=TZ, today=date(2026, 6, 4))
        notes = frame[date(2026, 6, 3)]["notes"]
        self.assertEqual(len(notes), 3)          # capped
        self.assertLessEqual(len(notes[0]), 80)  # truncated
        self.assertIn("anxious", notes[0])


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

    def test_wanted_test_survives_only_without_a_test(self):
        raw = """{"hypotheses": [
          {"hypothesis": "Rough months follow short cycles", "test": null,
           "wanted_test": "compare each cycle's length against luteal mood dip depth"},
          {"hypothesis": "Mood dips in luteal", 
           "test": {"type": "condition_compare", "metric": "mood", "condition": "phase:luteal"},
           "wanted_test": "should be ignored — a real test exists"}
        ]}"""
        parsed = _parse_hypotheses(raw)
        self.assertEqual(parsed[0][2], "compare each cycle's length against luteal mood dip depth")
        self.assertIsNone(parsed[1][2])

    def test_garbage_yields_nothing(self):
        # None = the call/parse FAILED (cursor must not advance);
        # [] = a genuine "nothing new" (cursor advances).
        self.assertIsNone(_parse_hypotheses("not json"))


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


class TestThemeRecurrence(unittest.TestCase):
    def test_returning_theme_verifies(self):
        verified, evidence, stats = verify({}, {
            "type": "theme_recurrence", "theme": "making music",
        }, theme_counter=lambda phrase, window: (7, ["drum machines seed",
                                                     "PO-33 research",
                                                     "Making Music effort"]))
        self.assertTrue(verified)
        self.assertIn("7 times", evidence)
        self.assertEqual(stats["count"], 7)

    def test_sparse_theme_keeps_gathering(self):
        verified, evidence, _ = verify({}, {
            "type": "theme_recurrence", "theme": "pottery",
        }, theme_counter=lambda phrase, window: (2, ["ceramics seed"]))
        self.assertFalse(verified)
        self.assertIn("keep gathering", evidence)

    def test_no_index_reports_itself(self):
        verified, evidence, _ = verify({}, {
            "type": "theme_recurrence", "theme": "anything",
        })
        self.assertFalse(verified)
        self.assertIn("can't count", evidence)


class TestWiderFrame(unittest.TestCase):
    def test_run_metrics_and_task_completions_land(self):
        uid = uuid4()
        d = date(2026, 6, 3)
        runs = [SimpleNamespace(ran_on=d, distance_km=6.4)]
        activities = [SimpleNamespace(
            start_time_epoch_seconds=int(datetime(2026, 6, 3, 17, 0,
                                                  tzinfo=timezone.utc).timestamp()),
            activity_type="running", average_heart_rate=168)]
        task_events = [SimpleNamespace(event_type="completed",
                                       occurred_at=datetime(2026, 6, 3, 9, 0,
                                                            tzinfo=timezone.utc))]
        frame = build_daily_frame(uid, states=[], events=[], health_rows=[],
                                  runs=runs, activities=activities,
                                  task_events=task_events, tz=TZ,
                                  today=date(2026, 6, 4))
        row = frame[d]
        self.assertEqual(row["ran_km"], 6.4)
        self.assertEqual(row["run_avg_hr"], 168)
        self.assertEqual(row["tasks_done"], 1)


class TestPatternResponseByWords(unittest.TestCase):
    class _Repo:
        def __init__(self, patterns):
            self._p = patterns

        def find_by_words(self, user_id, words):
            return [p for p in self._p if words.lower() in p["hypothesis"].lower()]

    class _Watcher:
        def __init__(self, repo):
            self._repo = repo
            self.resolved = []

        def respond(self, user_id, pattern_id, verdict, note):
            self.resolved.append((pattern_id, verdict))
            return {"hypothesis": "match"}

    def test_matches_single_pattern_by_phrase(self):
        pid = uuid4()
        w = self._Watcher(self._Repo([{"id": pid, "hypothesis": "Days with meds logged tend to have higher mood"}]))
        reply = handle_pattern_response(
            uuid4(), {"pattern": "meds", "verdict": "dismissed"},
            datetime.now(timezone.utc), watcher=w)
        self.assertIn("never come up again", reply)
        self.assertEqual(w.resolved[0][0], pid)

    def test_ambiguous_phrase_asks_which(self):
        w = self._Watcher(self._Repo([
            {"id": uuid4(), "hypothesis": "Stress higher during menstruation"},
            {"id": uuid4(), "hypothesis": "Stress lower after runs"},
        ]))
        reply = handle_pattern_response(
            uuid4(), {"pattern": "stress", "verdict": "dismissed"},
            datetime.now(timezone.utc), watcher=w)
        self.assertIn("which one", reply)
        self.assertEqual(w.resolved, [])


class TestTrend(unittest.TestCase):
    def test_falling_rhr_verifies(self):
        frame = _frame(30, lambda i: {"resting_hr": 53 - (i // 4)})
        verified, evidence, stats = verify(frame, {
            "type": "trend", "metric": "resting_hr", "direction": "down"})
        self.assertTrue(verified)
        self.assertIn("falling", evidence)
        self.assertLess(stats["mean_late"], stats["mean_early"])

    def test_wrong_direction_fails_honestly(self):
        frame = _frame(30, lambda i: {"resting_hr": 45 + (i // 4)})
        verified, evidence, _ = verify(frame, {
            "type": "trend", "metric": "resting_hr", "direction": "down"})
        self.assertFalse(verified)
        self.assertIn("OPPOSITE", evidence)

    def test_a_trend_with_no_direction_or_a_bad_one_confirms_nothing(self):
        """Correlations and comparisons had to state a direction; a trend still
        verified on any large enough change."""
        rising = _frame(30, lambda i: {"mood": 1.0 + i / 8})
        for spec in ({"type": "trend", "metric": "mood"},
                     {"type": "trend", "metric": "mood", "direction": "sideways"}):
            verified, evidence, stats = verify(rising, spec)
            self.assertFalse(verified)
            self.assertIn("names no direction", evidence)
            self.assertNotIn("error", stats)
        self.assertTrue(verify(rising, {"type": "trend", "metric": "mood", "direction": "up"})[0])

    def test_flat_metric_stays_silent(self):
        frame = _frame(30, lambda i: {"resting_hr": 50 + (i % 2)})
        verified, _, _ = verify(frame, {"type": "trend", "metric": "resting_hr"})
        self.assertFalse(verified)

    def test_too_few_days_keeps_gathering(self):
        frame = _frame(8, lambda i: {"resting_hr": 53 - i})
        verified, evidence, _ = verify(frame, {"type": "trend", "metric": "resting_hr"})
        self.assertFalse(verified)
        self.assertIn("keep gathering", evidence)


if __name__ == "__main__":
    unittest.main()


class TestEveryTrackedKindReachesTheFrame:
    """'Every tracked kind' was stored and never shown: the extra dimensions a
    state carries (anxiety, cramps, …) were left out of the daily frame, so
    neither sense_get's day view nor the Watcher's verification could see them."""

    def _frame(self, states):
        from datetime import date
        from zoneinfo import ZoneInfo
        from trellis.core_watcher import build_daily_frame
        return build_daily_frame(uuid4(), states=states, events=[], health_rows=[], runs=[],
                                 tz=ZoneInfo("UTC"), today=date(2026, 3, 11))

    def _state(self, day, extra, **scores):
        from datetime import datetime, timezone
        from trellis.domain_sense_models import StateLog
        when = datetime(2026, 3, day, 9, 0, tzinfo=timezone.utc)
        return StateLog(id=uuid4(), user_id=uuid4(), note="n", energy=scores.get("energy"),
                        mood=scores.get("mood"), felt_at=when, logged_at=when, extra=extra)

    def test_numeric_kinds_are_averaged_per_day_and_flags_are_kept(self):
        from datetime import date
        frame = self._frame([self._state(10, {"anxiety": 4, "cramps": True}), self._state(10, {"anxiety": 2}),
                             self._state(11, {"restless_legs": True})])
        assert frame[date(2026, 3, 10)]["anxiety"] == 3.0
        assert frame[date(2026, 3, 10)]["cramps"] is True
        assert frame[date(2026, 3, 11)]["restless_legs"] is True
        assert "anxiety" not in frame[date(2026, 3, 11)]             # absent means not logged, never zero

    def test_a_kind_cannot_overwrite_a_built_in_column(self):
        from datetime import date
        frame = self._frame([self._state(10, {"mood": 1, "sleep_hours": 99}, mood=4)])
        row = frame[date(2026, 3, 10)]
        assert row["mood"] == 4.0 and "sleep_hours" not in row
        assert row["tracked_mood"] == 1.0 and row["tracked_sleep_hours"] == 99.0

    def test_the_reserved_list_matches_what_the_frame_really_writes(self):
        """If the frame grows a column, a tracked kind of that name must not overwrite it."""
        import re
        from pathlib import Path
        from trellis.core_watcher import _FRAME_COLUMNS
        source = (Path(__file__).parent.parent / "src" / "trellis" / "core_watcher.py").read_text()
        body = source[source.index("def build_daily_frame("):]
        body = body[:body.index("\ndef ", 10)]
        written = set(re.findall(r'row\([^)]*\)\["([a-z_]+)"\]', body)) | set(re.findall(r'setdefault\("([a-z_]+)"', body))
        assert written <= _FRAME_COLUMNS, written - _FRAME_COLUMNS
