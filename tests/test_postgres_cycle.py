from __future__ import annotations

from datetime import date
from uuid import uuid4

from trellis.cycle import CycleService
from trellis.postgres import PostgresCycleRepository, PostgresDatabase


def _user(db: PostgresDatabase) -> object:
    return db.ensure_user(abs(hash(uuid4())) % (2**31), "Europe/Madrid")


def test_record_period_start_and_retrieve(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    service = CycleService(repo)
    user_id = _user(pg_database)

    event = service.record_period_start(user_id, date(2026, 6, 1), note="day 1")

    last = repo.last_period_start(user_id)
    assert last is not None
    assert last.id == event.id
    assert last.occurred_on == date(2026, 6, 1)
    assert last.note == "day 1"
    assert last.event_type == "period_start"


def test_last_period_start_returns_most_recent(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    service = CycleService(repo)
    user_id = _user(pg_database)

    service.record_period_start(user_id, date(2026, 5, 1))
    service.record_period_start(user_id, date(2026, 6, 1))

    last = repo.last_period_start(user_id)
    assert last.occurred_on == date(2026, 6, 1)


def test_last_period_start_none_when_no_data(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    user_id = _user(pg_database)

    assert repo.last_period_start(user_id) is None


def test_record_observation_with_symptoms(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    service = CycleService(repo)
    user_id = _user(pg_database)

    event = service.record_observation(
        user_id, date(2026, 6, 3),
        note="tired and crampy",
        symptoms=("fatigue", "cramps"),
    )

    events = repo.list_recent(user_id)
    obs = next(e for e in events if e.id == event.id)
    assert obs.event_type == "observation"
    assert "fatigue" in obs.symptoms
    assert "cramps" in obs.symptoms
    assert obs.note == "tired and crampy"


def test_list_recent_returns_in_desc_order(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    service = CycleService(repo)
    user_id = _user(pg_database)

    service.record_period_start(user_id, date(2026, 5, 1))
    service.record_period_start(user_id, date(2026, 6, 1))
    service.record_observation(user_id, date(2026, 6, 5), note="day 5")

    events = repo.list_recent(user_id, limit=2)
    assert len(events) == 2
    assert events[0].occurred_on >= events[1].occurred_on


def test_get_status_end_to_end(pg_database: PostgresDatabase):
    repo = PostgresCycleRepository(pg_database)
    service = CycleService(repo)
    user_id = _user(pg_database)

    service.record_period_start(user_id, date(2026, 6, 1))
    status = service.get_status(user_id, date(2026, 6, 10))

    assert "day 10" in status
    assert "follicular" in status
