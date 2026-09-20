"""One rule for the search index: it says what the records say. A seed is
indexed however it was created, re-indexed when its words change, and forgotten
when it stops being a seed — and a reconciliation pass repairs whatever drifted:
missing, stale, never embedded, or pointing at something that no longer exists."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.domain_focus_models import Task, TaskEnergy, TaskKind, TaskPriority, TaskStatus
from trellis.domain_focus_service import TaskService

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
UID = uuid4()


class _Memory:
    def __init__(self): self.cards = {}
    def remember(self, user_id, kind, entity_id, text): self.cards[(kind, entity_id)] = text
    def forget(self, kind, entity_id):
        self.cards.pop((kind, entity_id), None)
        return True


class _Tasks:
    def __init__(self): self.rows = {}
    def save(self, task):
        self.rows[task.id] = task
        return task
    def get(self, task_id): return self.rows.get(task_id)
    def update(self, task_id, **changes):
        from dataclasses import replace
        self.rows[task_id] = replace(self.rows[task_id], **changes)
        return self.rows[task_id]
    def list_open(self, user_id): return list(self.rows.values())


def _seed(title="why do cities form where they do"):
    return Task(id=uuid4(), user_id=UID, title=title, status=TaskStatus.OPEN, priority=TaskPriority.MEDIUM,
                energy=TaskEnergy.MEDIUM, kind=TaskKind.SEED, created_at=NOW, updated_at=NOW)


def _service(memory, repo=None):
    return TaskService(repo or _Tasks(), ZoneInfo("UTC"), memory=memory), repo


class TestASeedsWordsStayCurrent:
    def test_renaming_a_seed_reindexes_it(self):
        memory, repo = _Memory(), _Tasks()
        seed = repo.save(_seed())
        memory.remember(UID, "seed", seed.id, seed.embedding_text())
        TaskService(repo, ZoneInfo("UTC"), memory=memory).update(UID, seed.id, title="why ports become capitals", now=NOW)
        assert "ports become capitals" in memory.cards[("seed", seed.id)]
        assert "cities form" not in memory.cards[("seed", seed.id)]

    def test_a_seed_that_becomes_a_todo_leaves_the_index(self):
        memory, repo = _Memory(), _Tasks()
        seed = repo.save(_seed())
        memory.remember(UID, "seed", seed.id, seed.embedding_text())
        TaskService(repo, ZoneInfo("UTC"), memory=memory).update(UID, seed.id, kind=TaskKind.TODO, now=NOW)
        assert ("seed", seed.id) not in memory.cards

    def test_a_todo_that_becomes_a_seed_enters_it(self):
        from dataclasses import replace
        memory, repo = _Memory(), _Tasks()
        todo = repo.save(replace(_seed("read about ports"), kind=TaskKind.TODO))
        TaskService(repo, ZoneInfo("UTC"), memory=memory).update(UID, todo.id, kind=TaskKind.SEED, now=NOW)
        assert ("seed", todo.id) in memory.cards

    def test_a_seed_planted_by_a_brain_dump_is_indexed_like_any_other(self):
        from types import SimpleNamespace
        from trellis.domain_focus_models import CaptureType
        from trellis.domain_focus_service import BrainDumpService

        class Captures:
            def save(self, capture): return capture

        class Claude:
            def synthesise(self, raw, current_date_line, hints=None):
                return SimpleNamespace(
                    capture_type=CaptureType.IDEA, cleaned_text="clean", summary="s", effort_hints=(),
                    extracted_tasks=(SimpleNamespace(title="why do rivers braid", kind=TaskKind.SEED,
                                                     priority=TaskPriority.MEDIUM, energy=TaskEnergy.MEDIUM, due=None),))

        memory, tasks = _Memory(), _Tasks()
        BrainDumpService(Captures(), tasks, Claude(), ZoneInfo("UTC"), memory=memory).process(UID, "rambling", NOW)
        (seed,) = tasks.rows.values()
        assert "rivers braid" in memory.cards[("seed", seed.id)]


class TestReconciliation:
    """The repair pass: given what the records say, make the index agree."""

    def _index(self, rows):
        from trellis.infra_memory import plan_reconciliation
        return plan_reconciliation

    def test_it_finds_missing_stale_unembedded_and_orphaned_rows(self):
        from trellis.infra_memory import plan_reconciliation
        a, b, c, d, gone = (uuid4() for _ in range(5))
        expected = {("capture", a): (UID, "alpha"), ("capture", b): (UID, "beta NEW words"),
                    ("seed", c): (UID, "gamma"), ("effort", d): (UID, "delta")}
        indexed = {("capture", b): ("beta old words", True),      # stale: the record's words changed
                   ("seed", c): ("gamma", False),                 # never embedded (vectors were cleared)
                   ("effort", d): ("delta", True),                # fine
                   ("capture", gone): ("a record that was deleted", True)}   # orphan
        plan = plan_reconciliation(expected, indexed)
        assert {k for k, _ in plan.to_index} == {("capture", a), ("capture", b), ("seed", c)}
        assert plan.orphans == [("capture", gone)]
        assert (plan.missing, plan.stale, plan.unembedded) == (1, 1, 1)

    def test_an_index_that_agrees_needs_nothing(self):
        from trellis.infra_memory import plan_reconciliation
        a = uuid4()
        plan = plan_reconciliation({("capture", a): (UID, "alpha")}, {("capture", a): ("alpha", True)})
        assert plan.to_index == [] and plan.orphans == []
