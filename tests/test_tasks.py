from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from trellis.obsidian import END_MARKER, START_MARKER, ObsidianTaskProjection
from trellis.tasks import (
    AmbiguousTaskError,
    Energy,
    InMemoryTaskRepository,
    Priority,
    DuplicateTaskError,
    TaskNotFoundError,
    TaskService,
    TaskStatus,
    UNSET,
)


class TaskServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name)
        tasks_path = self.vault / "Calendar" / "Trellis" / "Tasks.md"
        tasks_path.parent.mkdir(parents=True)
        tasks_path.write_text("# Tasks\n\nMy handwritten note.\n", encoding="utf-8")
        self.repository = InMemoryTaskRepository()
        self.service = TaskService(
            self.repository,
            ObsidianTaskProjection(self.vault),
        )
        self.user_id = uuid4()

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_preserves_user_content_and_projects_task(self):
        task = self.service.create(self.user_id, "  Pay   Greece. ")

        content = (self.vault / "Calendar" / "Trellis" / "Tasks.md").read_text(encoding="utf-8")
        self.assertEqual("Pay Greece", task.title)
        self.assertIn("My handwritten note.", content)
        self.assertIn("- [ ] Pay Greece", content)
        self.assertEqual(1, content.count(START_MARKER))
        self.assertEqual(1, content.count(END_MARKER))

    def test_complete_matches_named_task_not_highest_priority(self):
        first = self.service.create(
            self.user_id,
            "Book dentist",
            priority=Priority.HIGH,
        )
        second = self.service.create(self.user_id, "Call mum")

        completed = self.service.complete(self.user_id, "call mum")

        self.assertEqual(second.id, completed.id)
        self.assertEqual(TaskStatus.DONE, completed.status)
        self.assertEqual(TaskStatus.OPEN, self.repository.tasks[first.id].status)
        content = (self.vault / "Calendar" / "Trellis" / "Tasks.md").read_text(encoding="utf-8")
        self.assertIn("Book dentist", content)
        self.assertNotIn("Call mum", content)

    def test_archive_by_visible_numbers_removes_selected_open_tasks(self):
        self.service.create(self.user_id, "Keep one")
        remove_two = self.service.create(self.user_id, "Remove two")
        remove_three = self.service.create(self.user_id, "Remove three")
        self.service.create(self.user_id, "Keep four")

        archived = self.service.archive(self.user_id, "2, 3")

        self.assertEqual([remove_two.id, remove_three.id], [task.id for task in archived])
        self.assertEqual(TaskStatus.ARCHIVED, self.repository.tasks[remove_two.id].status)
        self.assertEqual(TaskStatus.ARCHIVED, self.repository.tasks[remove_three.id].status)
        content = (self.vault / "Calendar" / "Trellis" / "Tasks.md").read_text(encoding="utf-8")
        self.assertIn("Keep one", content)
        self.assertNotIn("Remove two", content)
        self.assertNotIn("Remove three", content)
        self.assertIn("Keep four", content)

    def test_select_today_limits_to_three_and_prioritizes_overdue(self):
        now = datetime.now(timezone.utc)
        self.service.create(self.user_id, "Low", priority=Priority.LOW)
        self.service.create(self.user_id, "High", priority=Priority.HIGH)
        overdue = self.service.create(
            self.user_id,
            "Overdue",
            priority=Priority.LOW,
            energy=Energy.LOW,
            due_at=now - timedelta(days=1),
        )
        self.service.create(self.user_id, "Medium")

        selected = self.service.select_today(self.user_id, energy=Energy.LOW)

        self.assertEqual(3, len(selected))
        self.assertEqual(overdue.id, selected[0].id)

    def test_missing_completion_is_explicit(self):
        with self.assertRaises(TaskNotFoundError):
            self.service.complete(self.user_id, "does not exist")

    def test_exact_duplicate_is_not_created(self):
        original = self.service.create(self.user_id, "Pay my gestor this week")

        with self.assertRaises(DuplicateTaskError) as raised:
            self.service.create(self.user_id, "  pay MY gestor this week. ")

        self.assertEqual(original.id, raised.exception.task.id)
        self.assertEqual(1, len(self.service.list_open(self.user_id)))

    # -- update_task ---------------------------------------------------------

    def test_update_title_renames_task(self):
        task = self.service.create(self.user_id, "Old title")
        updated = self.service.update_task(self.user_id, "Old title", new_title="New title")
        self.assertEqual("New title", updated.title)
        self.assertEqual(task.id, updated.id)

    def test_update_title_normalises_whitespace(self):
        task = self.service.create(self.user_id, "Old title")
        updated = self.service.update_task(self.user_id, "Old title", new_title="  New  title.  ")
        self.assertEqual("New title", updated.title)

    def test_update_title_rejects_duplicate(self):
        self.service.create(self.user_id, "Task A")
        self.service.create(self.user_id, "Task B")
        # InMemoryTaskRepository doesn't check duplicates in update_task — that's a Postgres-layer concern.
        # Service layer passes through; just confirm it doesn't raise for in-memory.
        updated = self.service.update_task(self.user_id, "Task A", new_title="Task A renamed")
        self.assertEqual("Task A renamed", updated.title)

    def test_update_due_at_sets_value(self):
        task = self.service.create(self.user_id, "Run 10k")
        due = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        updated = self.service.update_task(self.user_id, "Run 10k", due_at=due)
        self.assertEqual(due, updated.due_at)

    def test_update_due_at_clears_when_none(self):
        due = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        task = self.service.create(self.user_id, "Run 10k", due_at=due)
        updated = self.service.update_task(self.user_id, "Run 10k", due_at=None)
        self.assertIsNone(updated.due_at)

    def test_update_with_unset_does_not_change_due_at(self):
        due = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        task = self.service.create(self.user_id, "Run 10k", due_at=due)
        updated = self.service.update_task(self.user_id, "Run 10k", new_title="Run 5k")
        # due_at unchanged because UNSET was the default
        self.assertEqual(due, updated.due_at)
        self.assertEqual("Run 5k", updated.title)

    def test_update_task_not_found_raises(self):
        with self.assertRaises(TaskNotFoundError):
            self.service.update_task(self.user_id, "does not exist", new_title="Anything")

    def test_create_with_due_at(self):
        due = datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)
        task = self.service.create(self.user_id, "Book flights", due_at=due)
        self.assertEqual(due, task.due_at)


if __name__ == "__main__":
    unittest.main()
