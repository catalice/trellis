"""Unit tests for core pieces: router defaults, oracle tool trace, obsidian vault."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.core_oracle import OracleResult, ToolCall
from trellis.core_router import Router
from trellis.domain_second_brain_models import (
    Capture,
    CaptureType,
    Effort,
    EffortIntensity,
    Reminder,
    Task,
    TaskEnergy,
    TaskPriority,
    TaskStatus,
)
from trellis.infra_obsidian import ObsidianVault

TZ = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
UID = uuid4()


class TestRouter:
    SIGNALS = {"second_brain": ["task", "remind"], "training": ["run", "garmin"]}

    def test_matches_signal(self):
        r = Router(self.SIGNALS, default_domain="second_brain")
        assert r.route("how was my run") == {"training"}

    def test_multiple_domains(self):
        r = Router(self.SIGNALS, default_domain="second_brain")
        assert r.route("task list before my run") == {"second_brain", "training"}

    def test_unmatched_goes_to_default(self):
        r = Router(self.SIGNALS, default_domain="second_brain")
        assert r.route("hello there") == {"second_brain"}

    def test_reminder_variants_route(self):
        r = Router(self.SIGNALS, default_domain="second_brain")
        assert r.route("could you add a reminder please") == {"second_brain"}
        assert r.route("remind me tomorrow") == {"second_brain"}

    def test_no_default_returns_empty(self):
        r = Router(self.SIGNALS)
        assert r.route("hello there") == set()


class TestOracleTrace:
    def test_trace_renders_calls(self):
        result = OracleResult("Done.", (
            ToolCall("create_task", "Task created: Buy dresses"),
            ToolCall("set_reminder", "Reminder set: Fitting"),
        ))
        assert result.trace() == (
            "[actions taken: create_task → Task created: Buy dresses; "
            "set_reminder → Reminder set: Fitting]"
        )

    def test_no_calls_no_trace(self):
        assert OracleResult("Just chat.").trace() is None


class _TaskRepo:
    def __init__(self, tasks):
        self._tasks = tasks

    def list_open(self, user_id):
        return [t for t in self._tasks if t.status == TaskStatus.OPEN]

    def list_parked(self, user_id):
        return [t for t in self._tasks if t.status == TaskStatus.PARKED]

    def list_recent(self, user_id, *, limit):
        return self._tasks[:limit]


class _ReminderRepo:
    def __init__(self, reminders):
        self._reminders = reminders

    def list_upcoming(self, user_id, *, before):
        return [r for r in self._reminders if r.remind_at <= before]


class _EffortRepo:
    def __init__(self, efforts):
        self._efforts = {e.id: e for e in efforts}

    def get(self, eid):
        return self._efforts.get(eid)


def _task(title, *, due=None, status=TaskStatus.OPEN, completed=None):
    return Task(id=uuid4(), user_id=UID, title=title, status=status,
                priority=TaskPriority.MEDIUM, energy=TaskEnergy.MEDIUM,
                due_at=due, completed_at=completed)


class TestObsidianVault:
    def _vault(self, tmp_path, tasks=(), reminders=(), efforts=()):
        return ObsidianVault(
            tmp_path, TZ,
            _TaskRepo(list(tasks)), _ReminderRepo(list(reminders)), _EffortRepo(list(efforts)),
        )

    def test_capture_appends_to_daily_note(self, tmp_path):
        vault = self._vault(tmp_path)
        capture = Capture(id=uuid4(), user_id=UID, raw="line one\nline two",
                          capture_type=CaptureType.IDEA, synthesis="Cleaned.",
                          summary="A thought", effort_id=None, created_at=NOW)
        vault.capture_saved(capture)
        vault.capture_saved(capture)  # second dump, same day
        note = (tmp_path / "Calendar" / "Captures" / "2026-07-20.md").read_text()
        assert note.startswith("# Monday 20 July 2026")
        assert note.count("## ") == 2
        assert "> line one" in note
        assert "Cleaned." in note

    def test_tasks_file_sections(self, tmp_path):
        # tasks_changed uses wall-clock time, so build dates relative to real now
        real_now = datetime.now(timezone.utc)
        end_of_today = real_now.astimezone(TZ).replace(hour=23, minute=59)
        tasks = [
            _task("Overdue thing", due=real_now - timedelta(days=1)),
            _task("Today thing", due=end_of_today),
            _task("Future thing", due=real_now + timedelta(days=3)),
            _task("Anytime thing"),
            _task("Done thing", status=TaskStatus.DONE, completed=real_now),
        ]
        reminders = [Reminder(id=uuid4(), user_id=UID, label="Fitting",
                              remind_at=real_now + timedelta(days=1), status="scheduled")]
        vault = self._vault(tmp_path, tasks=tasks, reminders=reminders)
        vault.tasks_changed(UID)
        content = (tmp_path / "Calendar" / "Tasks.md").read_text()
        for section in ("## Overdue", "## Today", "## Upcoming", "## Anytime",
                        "## Reminders", "## Recently completed"):
            assert section in content
        assert "- [x] Done thing" in content
        assert "🔔 Fitting" in content

    def test_empty_tasks_file_has_message(self, tmp_path):
        vault = self._vault(tmp_path)
        vault.tasks_changed(UID)
        assert "Nothing on the list" in (tmp_path / "Calendar" / "Tasks.md").read_text()

    def test_effort_page_and_capture_assignment(self, tmp_path):
        effort = Effort(id=uuid4(), user_id=UID, title="Notes from the Void",
                        intensity=EffortIntensity.ACTIVE, notes="Why are we here?",
                        obsidian_path="Efforts/Notes from the Void.md")
        vault = self._vault(tmp_path, efforts=[effort])
        vault.effort_created(effort)
        capture = Capture(id=uuid4(), user_id=UID, raw="raw", capture_type=CaptureType.IDEA,
                          synthesis=None, summary="Void thought", effort_id=effort.id,
                          created_at=NOW)
        vault.capture_assigned(capture)
        page = (tmp_path / "Efforts" / "Notes from the Void.md").read_text()
        assert page.startswith("# Notes from the Void")
        assert "[[2026-07-20]] — Void thought" in page

    def test_effort_page_not_overwritten(self, tmp_path):
        effort = Effort(id=uuid4(), user_id=UID, title="X",
                        intensity=EffortIntensity.ACTIVE, notes=None,
                        obsidian_path="Efforts/X.md")
        vault = self._vault(tmp_path, efforts=[effort])
        vault.effort_created(effort)
        page = tmp_path / "Efforts" / "X.md"
        page.write_text(page.read_text() + "\nCat's own notes here\n")
        vault.effort_created(effort)  # must not clobber
        assert "Cat's own notes here" in page.read_text()

    def test_write_failure_never_raises(self, tmp_path):
        vault = ObsidianVault(tmp_path / "missing", TZ, None, None, None)
        vault.tasks_changed(UID)  # repos are None → internal error → swallowed
