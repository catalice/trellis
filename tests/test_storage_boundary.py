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
