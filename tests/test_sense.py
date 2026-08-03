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
            "note": "slept badly, took dex at 9, feeling flat",
            "energy": 2, "mood": 3,
            "meds": [{"name": "dex", "time": "09:00"}],
            "sleep_hours": 6, "sleep_quality": "badly",
        })
        assert len(repo.states) == 1
        assert repo.states[0].note == "slept badly, took dex at 9, feeling flat"
        types = [e.event_type for e in repo.events]
        assert TrackingEventType.MEDS in types
        assert TrackingEventType.SLEEP in types
        meds = next(e for e in repo.events if e.event_type == TrackingEventType.MEDS)
        assert meds.detail == "dex"
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

    def test_bad_med_time_still_logs_med(self):
        reply, repo = self._handle({
            "note": "took meds", "meds": [{"name": "dex", "time": "nineish"}],
        })
        meds = [e for e in repo.events if e.event_type == TrackingEventType.MEDS]
        assert len(meds) == 1
        assert meds[0].occurred_at == NOW


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
