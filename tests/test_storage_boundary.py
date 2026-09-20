"""Behaviour proven against a real Postgres — the faults that fake repositories
hide. Skipped when Docker isn't reachable (see conftest.pg_database)."""
from __future__ import annotations

from datetime import date

from trellis.infra_tracking import GarminDailyHealthRecord, PostgresHealthRepository

DAY = date(2026, 3, 10)


class TestPartialGarminSync:
    """A sync where one Garmin endpoint failed returns that day with the failed
    fields missing. Missing must never erase a reading already stored."""

    def test_a_missing_field_keeps_the_stored_reading(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=4000,
            sleep_duration_minutes=432, sleep_score=81, hrv_last_night=55.0,
            raw={"steps": 4000, "sleep_score": 81, "sleep_deep_minutes": 70},
        ))
        # Later sync: the sleep and HRV requests failed, steps moved on.
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=9000,
            raw={"steps": 9000, "unavailable": ["sleep", "hrv"]},
        ))
        stored = repo.latest_daily_health(pg_user)
        assert stored.steps == 9000
        assert (stored.sleep_duration_minutes, stored.sleep_score, stored.hrv_last_night) == (432, 81, 55.0)
        assert stored.raw["sleep_deep_minutes"] == 70      # the raw sleep detail survives too
        assert stored.raw["unavailable"] == ["sleep", "hrv"]

    def test_a_new_reading_still_replaces_an_old_one(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_daily_health(GarminDailyHealthRecord(user_id=pg_user, observed_on=DAY, sleep_score=60))
        repo.upsert_daily_health(GarminDailyHealthRecord(user_id=pg_user, observed_on=DAY, sleep_score=74))
        assert repo.latest_daily_health(pg_user).sleep_score == 74

    def test_a_complete_later_sync_clears_the_unavailable_mark(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=1, raw={"steps": 1, "unavailable": ["sleep"]}))
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=2, sleep_score=70, raw={"steps": 2, "sleep_score": 70}))
        assert "unavailable" not in repo.latest_daily_health(pg_user).raw


class TestActivitySplitsSurviveStorage:
    """Garmin sends laps as {"lapDTOs": [...]}. They must come back out of the
    database as they went in — the coach reads laps from the stored detail."""

    RAW = {
        "splits": {"lapDTOs": [{"distance": 1000.0, "duration": 360.0, "averageHR": 141.0},
                               {"distance": 1000.0, "duration": 350.0, "averageHR": 155.0}]},
        "typedSplits": {"splits": [{"type": "INTERVAL_ACTIVE", "duration": 710.0}]},
    }

    def test_lap_object_round_trips(self, pg_database, pg_user):
        from trellis.infra_garmin import GarminActivityDetail
        from trellis.domain_move_service import _lap_rows
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_activity_detail(user_id=pg_user, activity_id="a1", raw_data=self.RAW, sync_run_id=None)
        stored = repo.get_activity_detail(pg_user, "a1")
        assert stored["splits"] == self.RAW["splits"]
        laps = _lap_rows(GarminActivityDetail(activity_id="a1", raw=stored))
        assert [lap["averageHR"] for lap in laps] == [141.0, 155.0]

    def test_a_plain_lap_list_still_round_trips(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        raw = {"splits": [{"distance": 1000.0, "duration": 360.0}]}
        repo.upsert_activity_detail(user_id=pg_user, activity_id="a2", raw_data=raw, sync_run_id=None)
        assert repo.get_activity_detail(pg_user, "a2")["splits"] == raw["splits"]


class TestPartialActivityDetail:
    """The worker names a failed section as '<key>Error' and leaves the section
    out. A failed section must keep what is stored — laps AND the raw payload
    they can be recovered from."""

    FULL = {
        "activity": {"summaryDTO": {"averageHR": 150}},
        "splits": {"lapDTOs": [{"distance": 1000.0, "duration": 360.0}]},
        "typedSplits": {"splits": [{"type": "INTERVAL_ACTIVE", "duration": 360.0}]},
        "splitSummaries": {"splitSummaries": [{"splitType": "RWD_RUN", "duration": 360.0}]},
    }

    def test_a_failed_section_keeps_the_stored_one_and_its_raw_source(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_activity_detail(user_id=pg_user, activity_id="p1", raw_data=self.FULL, sync_run_id=None)
        later = {"activity": {"summaryDTO": {"averageHR": 151}},
                 "typedSplits": self.FULL["typedSplits"],
                 "splitsError": "upstream timeout", "splitSummariesError": "upstream timeout"}
        failed = repo.upsert_activity_detail(user_id=pg_user, activity_id="p1", raw_data=later, sync_run_id=None)
        assert failed == ("splits", "splitSummaries")
        stored = repo.get_activity_detail(pg_user, "p1")
        assert stored["splits"] == self.FULL["splits"]
        assert stored["splitSummaries"] == self.FULL["splitSummaries"]
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT raw_data FROM garmin_activity_details WHERE activity_id = 'p1'")
            (raw,) = cur.fetchone()
        assert raw["splits"] == self.FULL["splits"]                 # the recovery source survives
        assert raw["activity"]["summaryDTO"]["averageHR"] == 151    # what did arrive is updated
        assert raw["unavailable"] == ["splits", "splitSummaries"]

    def test_a_complete_later_fetch_replaces_and_clears_the_mark(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_activity_detail(user_id=pg_user, activity_id="p2",
                                    raw_data={"splitsError": "x", "typedSplits": self.FULL["typedSplits"]},
                                    sync_run_id=None)
        assert repo.upsert_activity_detail(user_id=pg_user, activity_id="p2", raw_data=self.FULL, sync_run_id=None) == ()
        assert repo.get_activity_detail(pg_user, "p2")["splits"] == self.FULL["splits"]
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT raw_data ? 'unavailable' FROM garmin_activity_details WHERE activity_id = 'p2'")
            assert cur.fetchone() == (False,)


class TestKeptReadingsStaySaidToBeOld:
    """A reading kept because its refresh failed must not read as fresh later:
    the last good refresh of each group is recorded, and survives the merge."""

    def test_each_group_remembers_its_last_good_refresh(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=4000, sleep_score=81,
            raw={"steps": 4000, "sleep_score": 81,
                 "refreshed_at": {"stats": "2026-03-10T07:00:00+00:00", "sleep": "2026-03-10T07:00:00+00:00"}}))
        repo.upsert_daily_health(GarminDailyHealthRecord(
            user_id=pg_user, observed_on=DAY, steps=9000,
            raw={"steps": 9000, "unavailable": ["sleep"],
                 "refreshed_at": {"stats": "2026-03-10T19:00:00+00:00"}}))
        raw = repo.latest_daily_health(pg_user).raw
        assert raw["refreshed_at"] == {"stats": "2026-03-10T19:00:00+00:00",
                                       "sleep": "2026-03-10T07:00:00+00:00"}


class TestEveryWorkerFailureIsKept:
    def test_a_failed_section_without_its_own_column_is_still_reported_and_preserved(self, pg_database, pg_user):
        repo = PostgresHealthRepository(pg_database)
        first = {"activity": {"summaryDTO": {"averageHR": 150}}, "details": {"metrics": [1, 2, 3]},
                 "splits": {"lapDTOs": [{"distance": 1000.0, "duration": 360.0}]}}
        repo.upsert_activity_detail(user_id=pg_user, activity_id="e1", raw_data=first, sync_run_id=None)
        later = {"activityError": "timeout", "detailsError": "timeout", "splits": first["splits"]}
        failed = repo.upsert_activity_detail(user_id=pg_user, activity_id="e1", raw_data=later, sync_run_id=None)
        assert set(failed) == {"activity", "details"}
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT raw_data FROM garmin_activity_details WHERE activity_id = 'e1'")
            (raw,) = cur.fetchone()
        assert raw["activity"] == first["activity"] and raw["details"] == first["details"]
        assert set(raw["unavailable"]) == {"activity", "details"}
        assert set(repo.get_activity_detail(pg_user, "e1")["unavailable"]) == {"activity", "details"}


class TestDurableActionRecord:
    def test_an_attempt_is_on_record_before_it_is_closed_and_reads_unknown(self, pg_database, pg_user):
        from trellis.core_actions import PostgresActionLog, Status
        log = PostgresActionLog(pg_database, pg_user)
        handle = log.begin("save_note", {"text": "boiler"})
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT tool, status, finished_at, input->>'text' FROM action_log WHERE user_id = %s", (pg_user,))
            assert cur.fetchall() == [("save_note", "unknown", None, "boiler")]     # the process could die here
        log.finish(handle, Status.FAILED, "Save failed; nothing changed.")
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, summary, finished_at IS NOT NULL FROM action_log WHERE user_id = %s", (pg_user,))
            assert cur.fetchall() == [("failed", "Save failed; nothing changed.", True)]

    def test_an_attempt_that_cannot_be_recorded_is_reported_as_such_and_never_raises(self, pg_user):
        """`begin` returns None: the engine then does not start a changing action
        (tests/test_truthful_outcomes.py::TestNoRecordNoAction)."""
        from trellis.core_actions import PostgresActionLog

        class Down:
            def connect(self):
                raise ConnectionError("database is down")

        log = PostgresActionLog(Down(), pg_user)
        assert log.begin("save_note", {"text": "x"}) is None        # must not raise
        assert log.entries == []

class TestWatchPushRecord:
    def test_a_dated_session_is_remembered_and_a_correction_replaces_its_id(self, pg_database, pg_user):
        from datetime import date as _date
        from trellis.domain_move_repo import PostgresMoveRepository
        repo = PostgresMoveRepository(pg_database)
        monday, wednesday = _date(2026, 3, 9), _date(2026, 3, 11)
        assert repo.get_watch_push(pg_user, monday, "Easy Run") is None
        repo.record_watch_push(pg_user, monday, "Easy Run", "w1")
        repo.record_watch_push(pg_user, wednesday, "Easy Run", "w2")       # same name, another day
        repo.record_watch_push(pg_user, wednesday, "Easy Run", "w3")       # a correction
        assert repo.get_watch_push(pg_user, monday, "Easy Run") == "w1"
        assert repo.get_watch_push(pg_user, wednesday, "Easy Run") == "w3"


class TestReminderDeliveryStates:
    def _reminder(self, database, user):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        from trellis.domain_focus_models import Reminder
        from trellis.domain_focus_repo import PostgresReminderRepository
        repo = PostgresReminderRepository(database)
        now = datetime.now(timezone.utc)
        saved = repo.save(Reminder(id=uuid4(), user_id=user, label="check the oven",
                                   remind_at=now - timedelta(minutes=1), status="scheduled"))
        return repo, saved, now

    def test_only_one_claim_wins_and_a_claimed_reminder_is_no_longer_due(self, pg_database, pg_user):
        from datetime import timedelta
        repo, reminder, now = self._reminder(pg_database, pg_user)
        assert repo.claim(reminder.id, now=now) is True
        assert repo.claim(reminder.id, now=now) is False
        assert repo.list_upcoming(pg_user, before=now + timedelta(hours=1)) == []

    def test_the_message_is_stored_before_sending_and_acceptance_is_recorded(self, pg_database, pg_user):
        repo, reminder, now = self._reminder(pg_database, pg_user)
        repo.claim(reminder.id, now=now)
        repo.ready(reminder.id, "Reminder: check the oven")
        (waiting,) = repo.awaiting_delivery(pg_user)
        assert (waiting.status, waiting.message, waiting.attempts) == ("executed", "Reminder: check the oven", 0)
        assert repo.delivery_failed(reminder.id) == 1
        repo.accepted(reminder.id, now=now)
        assert repo.awaiting_delivery(pg_user) == []
        assert repo.get(reminder.id).status == "accepted"

    def test_a_cancelled_or_already_claimed_reminder_cannot_be_made_ready_again(self, pg_database, pg_user):
        repo, reminder, now = self._reminder(pg_database, pg_user)
        repo.claim(reminder.id, now=now)
        repo.ready(reminder.id, "first")
        repo.ready(reminder.id, "second")                   # a second 'ready' must not replace the stored reply
        assert repo.get(reminder.id).message == "first"


class TestEraseAtTheStorageBoundary:
    def test_a_capture_can_be_read_back_before_it_is_erased(self, pg_database, pg_user):
        from datetime import datetime, timezone
        from uuid import uuid4
        from trellis.domain_focus_models import Capture, CaptureType
        from trellis.domain_focus_repo import PostgresCaptureRepository
        repo = PostgresCaptureRepository(pg_database)
        saved = repo.save(Capture(id=uuid4(), user_id=pg_user, raw="a private thought", capture_type=CaptureType.IDEA,
                                  synthesis=None, summary="Private", effort_id=None,
                                  created_at=datetime.now(timezone.utc)))
        assert repo.get(pg_user, saved.id).raw == "a private thought"
        assert repo.get(uuid4(), saved.id) is None                  # never another person's
        assert repo.delete(pg_user, saved.id) and repo.get(pg_user, saved.id) is None

    def test_the_action_log_keeps_thirty_days(self, pg_database, pg_user):
        from trellis.core_actions import PostgresActionLog
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO action_log (id, user_id, tool, input, started_at) VALUES"
                        " (gen_random_uuid(), %s, 'old', '{}'::jsonb, NOW() - INTERVAL '45 days'),"
                        " (gen_random_uuid(), %s, 'recent', '{}'::jsonb, NOW() - INTERVAL '5 days')", (pg_user, pg_user))
        PostgresActionLog(pg_database, pg_user).begin("new", {})
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT tool FROM action_log WHERE user_id = %s ORDER BY started_at", (pg_user,))
            assert [r[0] for r in cur.fetchall()] == ["recent", "new"]


class TestRollbackReconcilesInFlightReminders:
    """Rolling the code back must not strand a reminder the new code had picked
    up — and must never cause a check-in's actions to run again."""

    def test_in_flight_reminders_become_deliverable_by_stage_one_without_any_rerun(self, pg_database, pg_user):
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        from uuid import uuid4
        from trellis.domain_focus_models import Reminder
        from trellis.domain_focus_repo import PostgresReminderRepository
        repo = PostgresReminderRepository(pg_database)
        now = datetime.now(timezone.utc)

        def add(label, kind, *, recurrence=None):
            return repo.save(Reminder(id=uuid4(), user_id=pg_user, label=label, kind=kind, recurrence=recurrence,
                                      remind_at=now - timedelta(minutes=1), status="scheduled"))

        plain, ran, interrupted = add("check the oven", "remind"), add("weekly review", "check_in", recurrence="weekly"), \
            add("ask about the run", "check_in")
        untouched = add("tomorrow's thing", "remind")
        for r in (plain, ran, interrupted):
            repo.claim(r.id, now=now)
        repo.ready(ran.id, "Here's how the week went — three runs, all easy.")

        sql = (Path(__file__).parent.parent / "scripts" / "rollback_stages_2_3.sql").read_text()
        with pg_database.connect() as conn, conn.cursor() as cur:
            cur.execute(sql)

        after = {r.id: r for r in (repo.get(x.id) for x in (plain, ran, interrupted, untouched))}
        assert all(r.status == "scheduled" for r in after.values())          # stage 1 will see every one
        assert all(r.kind == "remind" for r in after.values())               # none will wake the model
        assert after[plain.id].label == "check the oven"
        assert after[ran.id].label == "Here's how the week went — three runs, all easy."   # its stored reply, verbatim
        assert after[ran.id].recurrence is None                              # the next weekly one already exists
        assert "interrupted" in after[interrupted.id].label and "not been run again" in after[interrupted.id].label
        assert after[untouched.id].label == "tomorrow's thing"
