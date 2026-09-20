"""Unit tests for the sense domain — wellbeing tracking (state logs, meds/sleep/
period events, cycle day) and the log_state handler.

Uses in-memory fake repos; no DB, no API calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.domain_sense_models import TrackingEventType
from trellis.domain_sense_service import SenseService
from trellis.domain_sense_tool import handle_log_state

TZ = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)  # Monday
UID = uuid4()


class FakeStateRepo:
    def __init__(self):
        self.states: list = []
        self.events: list = []

    def save_state(self, log):
        self.states.append(log)
        return log

    def save_event(self, event):
        self.events.append(event)
        return event

    def list_states_since(self, user_id, *, since):
        return sorted(
            (s for s in self.states if s.felt_at >= since),
            key=lambda s: s.felt_at,
        )

    def list_events_since(self, user_id, *, since):
        return [e for e in self.events if e.occurred_at >= since]

    def last_period_start(self, user_id):
        starts = [e for e in self.events if e.event_type == TrackingEventType.PERIOD_START]
        return max(starts, key=lambda e: e.occurred_at) if starts else None

    def entry_day(self, user_id, entry_id):
        found = [s.felt_at for s in self.states if s.id == entry_id] + \
                [e.occurred_at for e in self.events if e.id == entry_id]
        return found[0] if found else None

    def delete_state(self, user_id, log_id):
        before = len(self.states)
        self.states = [s for s in self.states if s.id != log_id]
        return len(self.states) < before

    def delete_event(self, user_id, event_id):
        before = len(self.events)
        self.events = [e for e in self.events if e.id != event_id]
        return len(self.events) < before


class TestSenseService:
    def _service(self, repo=None):
        return SenseService(repo or FakeStateRepo(), TZ)

    def test_log_state_stores_note_verbatim(self):
        svc = self._service()
        log = svc.log_state(UID, "dead this morning but weirdly cheerful",
                            energy=2, mood=4, now=NOW)
        assert log.note == "dead this morning but weirdly cheerful"
        assert log.energy == 2
        assert log.mood == 4

    def test_scores_clamped_to_range(self):
        svc = self._service()
        log = svc.log_state(UID, "x", energy=9, mood=0, now=NOW)
        assert log.energy == 5
        assert log.mood == 1

    def test_scores_optional(self):
        svc = self._service()
        log = svc.log_state(UID, "just noting", energy=None, mood=None, now=NOW)
        assert log.energy is None and log.mood is None

    def test_today_summary_compact_line(self):
        repo = FakeStateRepo()
        svc = self._service(repo)
        morning = NOW.astimezone(TZ).replace(hour=9, minute=12).astimezone(timezone.utc)
        evening = NOW.astimezone(TZ).replace(hour=19, minute=30).astimezone(timezone.utc)
        svc.log_state(UID, "rough", energy=2, mood=4, now=morning)
        svc.log_state(UID, "flying", energy=4, mood=5, now=evening)
        summary = svc.today_summary(UID, evening)
        assert summary == "State today: 09:12 e2/m4, 19:30 e4/m5"

    def test_today_summary_none_when_empty(self):
        assert self._service().today_summary(UID, NOW) is None

    def test_cycle_day(self):
        repo = FakeStateRepo()
        svc = self._service(repo)
        svc.log_event(UID, TrackingEventType.PERIOD_START,
                      occurred_at=NOW - timedelta(days=3))
        assert svc.cycle_day(UID, NOW) == 4

    def test_cycle_day_none_without_period(self):
        assert self._service().cycle_day(UID, NOW) is None

    def test_cycle_day_none_when_stale(self):
        repo = FakeStateRepo()
        svc = self._service(repo)
        svc.log_event(UID, TrackingEventType.PERIOD_START,
                      occurred_at=NOW - timedelta(days=90))
        assert svc.cycle_day(UID, NOW) is None


class TestLogStateHandler:
    def _handle(self, input_dict, repo=None):
        repo = repo or FakeStateRepo()
        svc = SenseService(repo, TZ)
        reply = handle_log_state(UID, input_dict, NOW, sense_service=svc, tz=TZ)
        return reply, repo

    def test_full_checkin(self):
        reply, repo = self._handle({
            "note": "slept badly, took ibuprofen at 9, feeling flat",
            "energy": 2, "mood": 3,
            "meds": [{"name": "ibuprofen", "time": "09:00"}],
            "sleep_hours": 6, "sleep_quality": "badly",
        })
        assert len(repo.states) == 1
        assert repo.states[0].note == "slept badly, took ibuprofen at 9, feeling flat"
        types = [e.event_type for e in repo.events]
        assert TrackingEventType.MEDS in types
        assert TrackingEventType.SLEEP in types
        meds = next(e for e in repo.events if e.event_type == TrackingEventType.MEDS)
        assert meds.detail == "ibuprofen"
        assert meds.occurred_at.astimezone(TZ).hour == 9

    def test_period_started(self):
        reply, repo = self._handle({"note": "period started", "period": "started"})
        assert [e.event_type for e in repo.events] == [TrackingEventType.PERIOD_START]

    def test_backdated_period_event(self):
        # The 3 Aug mess: history from Flo got stamped "now" (no date field) and
        # fabricated phantom state rows (note was forced). Now: period_date
        # backdates the event, and no note means no state row.
        reply, repo = self._handle({"period": "started", "period_date": "2026-04-20"})
        assert "Period started (2026-04-20)" in reply
        assert "State logged" not in reply
        event = repo.events[-1]
        assert event.occurred_at.astimezone(TZ).date().isoformat() == "2026-04-20"
        assert not repo.states  # no phantom state row

    def test_bad_period_date_logs_nothing(self):
        reply, repo = self._handle({"period": "started", "period_date": "April 20th"})
        assert "isn't a valid" in reply
        assert not repo.events

    def test_note_required(self):
        reply, repo = self._handle({"energy": 3})
        assert repo.states == []
        assert "Nothing to log" in reply

    def test_a_med_time_that_cannot_be_read_is_refused_and_nothing_is_logged(self):
        """It used to fall back to NOW in silence while the receipt echoed the
        unreadable time back as if it had been used."""
        from trellis.core_actions import Status, status_of
        reply, repo = self._handle({
            "note": "took meds", "meds": [{"name": "ibuprofen", "time": "nineish"}],
        })
        assert status_of(reply) is Status.FAILED and "nineish" in reply
        assert repo.events == [] and repo.states == []              # all or nothing


class TestOneAccountLandsOnOneDay:
    """'Yesterday I felt low, took my meds at nine and slept six hours' is ONE
    account of ONE day. The state went to yesterday while the meds and the sleep
    went to today — which corrupts every later comparison."""

    YESTERDAY = "2026-07-19T21:00"

    def _handle(self, input_dict):
        repo = FakeStateRepo()
        reply = handle_log_state(UID, input_dict, NOW, sense_service=SenseService(repo, TZ), tz=TZ)
        return reply, repo

    def test_state_meds_and_sleep_all_land_on_the_day_it_was_felt(self):
        reply, repo = self._handle({"note": "felt low", "mood": 2, "felt_at": self.YESTERDAY,
                                    "meds": [{"name": "ibuprofen", "time": "09:00"}], "sleep_hours": 6})
        days = {s.felt_at.astimezone(TZ).date().isoformat() for s in repo.states} | \
               {e.occurred_at.astimezone(TZ).date().isoformat() for e in repo.events}
        assert days == {"2026-07-19"}
        med = next(e for e in repo.events if e.event_type == TrackingEventType.MEDS)
        assert med.occurred_at.astimezone(TZ).strftime("%H:%M") == "09:00"
        assert "19 Jul" in reply                                        # the receipt says which day

    def test_backdating_works_for_events_alone_with_no_state(self):
        reply, repo = self._handle({"felt_at": self.YESTERDAY, "meds": [{"name": "ibuprofen"}]})
        (med,) = repo.events
        assert med.occurred_at.astimezone(TZ).date().isoformat() == "2026-07-19" and repo.states == []

    def test_an_unreadable_felt_at_is_refused_not_quietly_made_today(self):
        from trellis.core_actions import Status, status_of
        reply, repo = self._handle({"note": "felt low", "felt_at": "yesterday evening"})
        assert status_of(reply) is Status.FAILED and "yesterday evening" in reply
        assert repo.states == [] and repo.events == []

    def test_a_plain_checkin_today_is_unchanged(self):
        reply, repo = self._handle({"note": "fine", "meds": [{"name": "ibuprofen", "time": "08:15"}], "sleep_hours": 7})
        assert {e.occurred_at.astimezone(TZ).date() for e in repo.events} == {NOW.astimezone(TZ).date()}
        assert "19 Jul" not in reply and "20 Jul" not in reply          # no date noise when it's today


class TestFeltAtAndDelete:
    def test_retro_log_uses_felt_at(self):
        repo = FakeStateRepo()
        svc = SenseService(repo, TZ)
        felt = NOW - timedelta(hours=3)
        log = svc.log_state(UID, "this morning was shit", energy=1, mood=2,
                            now=NOW, felt_at=felt)
        assert log.felt_at == felt
        assert log.logged_at == NOW

    def test_felt_at_defaults_to_now(self):
        svc = SenseService(FakeStateRepo(), TZ)
        log = svc.log_state(UID, "now", energy=3, mood=3, now=NOW)
        assert log.felt_at == NOW

    def test_delete_entry_removes_state(self):
        repo = FakeStateRepo()
        svc = SenseService(repo, TZ)
        log = svc.log_state(UID, "wrong", energy=3, mood=3, now=NOW)
        assert svc.delete_entry(UID, log.id) is True
        assert repo.states == []
        assert svc.delete_entry(UID, log.id) is False


class TestHealthStaleness:
    """Stale readiness must be LOUD (the 2 Aug bug: yesterday's sleep 93 / HRV 70
    presented as 'your readiness is great' at 08:54 the next morning). Python
    computes stale_days; the formatted line instructs the model to sync or disclose."""

    class _FakeHealthReader:
        def __init__(self, observed_on):
            from types import SimpleNamespace
            self._h = SimpleNamespace(
                observed_on=observed_on, sleep_score=93, sleep_duration_minutes=480,
                resting_heart_rate=47, hrv_last_night=70, hrv_status="BALANCED",
                body_battery_maximum=85, body_battery_end=None, average_stress=19,
            )
        def latest_daily_health(self, user_id):
            return self._h

    def _service(self, observed_on):
        return SenseService(FakeStateRepo(), TZ, health_reader=self._FakeHealthReader(observed_on))

    def test_todays_record_is_fresh(self):
        svc = self._service(NOW.astimezone(TZ).date())
        health = svc.recent_health(UID, now=NOW)
        assert health["stale_days"] == 0
        from trellis.domain_sense_tool import _fmt_health
        line = _fmt_health(health)
        assert "TODAY" in line and "STALE" not in line

    def test_yesterdays_record_screams_stale(self):
        svc = self._service(NOW.astimezone(TZ).date() - timedelta(days=1))
        health = svc.recent_health(UID, now=NOW)
        assert health["stale_days"] == 1
        from trellis.domain_sense_tool import _fmt_health
        line = _fmt_health(health)
        assert "STALE" in line and "YESTERDAY" in line and "sync_garmin" in line

    def test_no_now_keeps_old_behaviour(self):
        svc = self._service(NOW.astimezone(TZ).date() - timedelta(days=1))
        health = svc.recent_health(UID)
        assert "stale_days" not in health

    def test_body_battery_is_the_level_not_the_days_max(self):
        """15 Sep: the line quoted the day's MAXIMUM as 'body battery' — 99 on an
        evening they were at 15. The level is the last reading; the peak is context."""
        from trellis.domain_sense_tool import _fmt_health
        line = _fmt_health({"date": "2026-09-14", "body_battery_end": 15, "body_battery_high": 99})
        assert "body battery 15 as of last watch sync, peaked at 99" in line
        assert "body battery 99 as" not in line

    def test_body_battery_falls_back_to_max_when_no_end(self):
        from trellis.domain_sense_tool import _fmt_health
        line = _fmt_health({"date": "2026-09-14", "body_battery_high": 85})
        assert "body battery 85 as of last watch sync" in line and "peaked" not in line


class TestCycleSummary:
    """5 Sep: nine months of cycle history was invisible to the fast mind five
    when it mattered. The maths is Python's, computed from the log."""

    class _Repo:
        def __init__(self, starts):
            self.starts = starts
        def period_starts(self, uid):
            return self.starts

    def _svc(self, starts):
        from zoneinfo import ZoneInfo
        from trellis.domain_sense_service import SenseService
        return SenseService(self._Repo(starts), ZoneInfo("UTC"))

    def test_average_and_next_expected(self):
        from datetime import date as d
        starts = [d(2026, 6, 16), d(2026, 7, 11), d(2026, 8, 7), d(2026, 9, 5)]
        c = self._svc(starts).cycle_summary("uid")
        assert c["avg_days"] == 27.0          # 25, 27, 29 -> 81/3
        assert c["next_expected"] == d(2026, 10, 2)
        assert c["window_start"] == d(2026, 9, 30)
        assert c["window_end"] == d(2026, 10, 4)

    def test_glitch_gaps_ignored(self):
        from datetime import date as d
        starts = [d(2026, 6, 1), d(2026, 6, 3), d(2026, 6, 29)]  # 2d glitch
        c = self._svc(starts).cycle_summary("uid")
        assert c["min_days"] == 26

    def test_single_start_returns_none(self):
        from datetime import date as d
        assert self._svc([d(2026, 9, 5)]).cycle_summary("uid") is None


class TestLoggedSummary:
    """The computed 30-day line: absence must be visible, so the last date rides along."""

    def _svc(self):
        return SenseService(FakeStateRepo(), TZ)

    def test_one_dose_long_ago_reads_as_one_day_and_how_long_since(self):
        svc = self._svc()
        svc.log_event(UID, TrackingEventType.MEDS, detail="Vitamin D 1000iu",
                      occurred_at=NOW - timedelta(days=6))
        rows = svc.logged_summary(UID, days=30, now=NOW)
        assert rows == [{"name": "vitamin d", "days": 1,
                         "last": (NOW - timedelta(days=6)).astimezone(TZ).date(), "days_ago": 6}]

    def test_doses_group_by_name_and_count_days_not_rows(self):
        svc = self._svc()
        for back in (1, 1, 3):
            svc.log_event(UID, TrackingEventType.MEDS, detail="magnesium 200mg",
                          occurred_at=NOW - timedelta(days=back))
        svc.log_event(UID, TrackingEventType.MEDS, detail="magnesium",
                      occurred_at=NOW - timedelta(days=5))
        (row,) = svc.logged_summary(UID, days=30, now=NOW)
        assert (row["name"], row["days"], row["days_ago"]) == ("magnesium", 3, 1)

    def test_extra_kinds_count_and_sleep_is_left_out(self):
        svc = self._svc()
        svc.log_state(UID, "tight chest", energy=None, mood=None, extra={"anxiety": 3}, now=NOW)
        svc.log_event(UID, TrackingEventType.SLEEP, value=7.0, occurred_at=NOW)
        assert [r["name"] for r in svc.logged_summary(UID, days=30, now=NOW)] == ["anxiety"]

    def test_outside_the_window_is_gone(self):
        svc = self._svc()
        svc.log_event(UID, TrackingEventType.MEDS, detail="magnesium",
                      occurred_at=NOW - timedelta(days=40))
        assert svc.logged_summary(UID, days=30, now=NOW) == []


class TestKeptReadingsAreNamedAsOld:
    """Hours after a sync that didn't bring a reading — the request failed, or it
    answered with nothing — the context line must still say that number is older
    than the sync, and when it was last really fetched."""

    class _Health:
        def __init__(self, record):
            self._record = record
        def latest_daily_health(self, uid):
            return self._record

    MORNING = "2026-07-20T05:10:00+00:00"

    def _line(self, refreshed_at):
        from types import SimpleNamespace
        from trellis.domain_sense_tool import _fmt_health
        record = SimpleNamespace(
            observed_on=NOW.astimezone(TZ).date(), sleep_score=81, sleep_duration_minutes=432,
            resting_heart_rate=52, hrv_last_night=55.0, hrv_status=None, body_battery_maximum=90,
            body_battery_end=80, average_stress=30, updated_at=NOW,
            raw={} if refreshed_at is None else {"refreshed_at": refreshed_at})
        svc = SenseService(FakeStateRepo(), TZ, health_reader=self._Health(record))
        return _fmt_health(svc.recent_health(UID, now=NOW))

    def _all_fresh(self):
        return {k: NOW.isoformat() for k in ("sleep_score", "sleep_duration_minutes", "hrv_last_night",
                                             "body_battery_end", "body_battery_max", "resting_hr", "stress_avg")}

    def test_an_empty_answer_with_no_error_still_leaves_the_reading_old(self):
        """The evening sync answered, carried no body battery, raised nothing.
        The morning's 80 must not read as the evening's."""
        stamps = {**self._all_fresh(), "body_battery_end": self.MORNING, "body_battery_max": self.MORNING}
        line = self._line(stamps)
        assert "NOT refreshed at the last sync" in line
        assert "body battery (last good 07:10)" in line
        assert "sleep (" not in line and "HRV (" not in line        # the fresh ones aren't named

    def test_a_reading_never_stamped_is_named_as_unknown(self):
        stamps = self._all_fresh()
        del stamps["hrv_last_night"]
        assert "HRV (last good unknown)" in self._line(stamps)

    def test_a_clean_sync_says_nothing_extra(self):
        assert "NOT refreshed" not in self._line(self._all_fresh())

    def test_a_row_from_before_stamps_existed_makes_no_claim(self):
        assert "NOT refreshed" not in self._line(None)
