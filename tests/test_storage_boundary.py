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
