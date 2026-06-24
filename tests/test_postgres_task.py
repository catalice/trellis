from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from trellis.postgres import PostgresDatabase, PostgresTaskRepository
from trellis.tasks import UNSET, TaskStatus


def _user(db: PostgresDatabase) -> object:
    return db.ensure_user(abs(hash(uuid4())) % (2**31), "Europe/Madrid")


# ---------------------------------------------------------------------------
# update_task — title
# ---------------------------------------------------------------------------

def test_update_title(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    task = repo.create(_task(user_id, "Original title"))

    updated = repo.update_task(task.id, new_title="Renamed title")

    assert updated.title == "Renamed title"
    assert updated.id == task.id
    assert updated.status == TaskStatus.OPEN


def test_update_title_duplicate_raises(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    repo.create(_task(user_id, "Task Alpha"))
    task_b = repo.create(_task(user_id, "Task Beta"))

    with pytest.raises(ValueError, match="already exists"):
        repo.update_task(task_b.id, new_title="Task Alpha")


def test_update_title_same_name_is_not_duplicate(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    task = repo.create(_task(user_id, "Same name"))

    # Updating to the same title should not raise
    updated = repo.update_task(task.id, new_title="Same name")
    assert updated.title == "Same name"


# ---------------------------------------------------------------------------
# update_task — due_at
# ---------------------------------------------------------------------------

def test_update_due_at_sets_value(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    task = repo.create(_task(user_id, "Set due date"))
    due = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)

    updated = repo.update_task(task.id, due_at=due)

    assert updated.due_at is not None
    assert updated.due_at.replace(tzinfo=timezone.utc) == due or updated.due_at == due


def test_update_due_at_clears_with_none(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    due = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    task = repo.create(_task(user_id, "Clear due date", due_at=due))

    updated = repo.update_task(task.id, due_at=None)

    assert updated.due_at is None


def test_update_unset_does_not_change_due_at(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    due = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    task = repo.create(_task(user_id, "Preserve due", due_at=due))

    updated = repo.update_task(task.id, new_title="Preserve due renamed")

    assert updated.title == "Preserve due renamed"
    assert updated.due_at is not None


def test_update_archived_task_raises(pg_database: PostgresDatabase):
    repo = PostgresTaskRepository(pg_database)
    user_id = _user(pg_database)
    task = repo.create(_task(user_id, "Will be archived"))
    repo.archive(task.id)

    with pytest.raises(LookupError):
        repo.update_task(task.id, new_title="After archive")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(user_id, title: str, *, due_at=None):
    from trellis.tasks import Energy, Priority, Task
    return Task(
        id=uuid4(),
        user_id=user_id,
        title=title,
        due_at=due_at,
        created_at=datetime.now(timezone.utc),
    )
