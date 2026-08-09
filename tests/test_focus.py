"""Unit tests for the second brain domain — parsers, services, models.

Uses in-memory fake repos; no DB, no API calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trellis.domain_focus_claude import (
    _parse_effort_suggestions,
    _parse_synthesis,
    _strip_json_fences,
)
from trellis.domain_focus_models import (
    BrainDumpResult,
    Capture,
    CaptureType,
    Effort,
    EffortIntensity,
    ExtractedTask,
    Goal,
    GoalStatus,
    GoalType,
    Reminder,
    Task,
    TaskEnergy,
    TaskEvent,
    TaskPriority,
    TaskStatus,
)
from trellis.domain_focus_service import (
    BrainDumpService,
    CleanupService,
    GoalNotFoundError,
    GoalService,
    ReminderService,
    TaskNotFoundError,
    TaskService,
    _parse_local_due,
)

TZ = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)  # Monday
UID = uuid4()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTaskRepo:
    def __init__(self):
        self.tasks: dict = {}
        self.events: list[TaskEvent] = []

    def save(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def list_open(self, user_id):
        return [t for t in self.tasks.values()
                if t.user_id == user_id and t.status == TaskStatus.OPEN]

    def list_parked(self, user_id):
        return [t for t in self.tasks.values()
                if t.user_id == user_id and t.status == TaskStatus.PARKED]

    def list_recent(self, user_id, *, limit):
        return list(self.tasks.values())[:limit]

    def update(self, task_id, **kwargs):
        from dataclasses import replace
        task = self.tasks[task_id]
        updated = replace(task, **kwargs)
        self.tasks[task_id] = updated
        return updated

    def delete(self, user_id, task_id):
        if task_id in self.tasks and self.tasks[task_id].user_id == user_id:
            del self.tasks[task_id]
            return True
        return False

    def save_event(self, event: TaskEvent) -> None:
        self.events.append(event)


class FakeCaptureRepo:
    def __init__(self):
        self.captures: dict = {}
        self.archived: set = set()

    def save(self, capture: Capture) -> Capture:
        self.captures[capture.id] = capture
        return capture

    def get(self, capture_id):
        return self.captures.get(capture_id)

    def list_recent(self, user_id, *, limit):
        return list(self.captures.values())[:limit]

    def list_unassigned(self, user_id, *, since):
        return [c for c in self.captures.values()
                if c.effort_id is None and c.id not in self.archived]

    def assign_to_effort(self, capture_id, effort_id):
        from dataclasses import replace
        c = replace(self.captures[capture_id], effort_id=effort_id)
        self.captures[capture_id] = c
        return c

    def archive(self, capture_id) -> None:
        self.archived.add(capture_id)

    def delete(self, user_id, capture_id):
        if capture_id in self.captures and self.captures[capture_id].user_id == user_id:
            del self.captures[capture_id]
            return True
        return False


class FakeReminderRepo:
    def __init__(self):
        self.reminders: dict = {}

    def save(self, r: Reminder) -> Reminder:
        self.reminders[r.id] = r
        return r

    def get(self, rid):
        return self.reminders.get(rid)

    def list_upcoming(self, user_id, *, before):
        return [r for r in self.reminders.values()
                if r.status == "scheduled" and r.remind_at <= before]

    def list_scheduled(self, user_id):
        return sorted(
            (r for r in self.reminders.values() if r.status == "scheduled"),
            key=lambda r: r.remind_at,
        )

    def cancel(self, rid) -> None:
        from dataclasses import replace
        self.reminders[rid] = replace(self.reminders[rid], status="cancelled")

    def mark_sent(self, rid) -> None:
        from dataclasses import replace
        self.reminders[rid] = replace(self.reminders[rid], status="sent")

    def list_recent(self, user_id, *, limit):
        return sorted(self.reminders.values(), key=lambda r: r.remind_at, reverse=True)[:limit]


class FakeGoalRepo:
    def __init__(self):
        self.goals: dict = {}

    def save(self, g: Goal) -> Goal:
        self.goals[g.id] = g
        return g

    def get(self, gid):
        return self.goals.get(gid)

    def list_active(self, user_id):
        return [g for g in self.goals.values() if g.status == GoalStatus.ACTIVE]

    def update(self, gid, **kwargs):
        from dataclasses import replace
        g = replace(self.goals[gid], **kwargs)
        self.goals[gid] = g
        return g


class FakeEffortRepo:
    def __init__(self):
        self.efforts: dict = {}

    def save(self, e: Effort) -> Effort:
        self.efforts[e.id] = e
        return e

    def get(self, eid):
        return self.efforts.get(eid)

    def get_by_title(self, user_id, title):
        for e in self.efforts.values():
            if e.user_id == user_id and e.title.lower() == title.strip().lower():
                return e
        return None

    def list_all(self, user_id):
        return list(self.efforts.values())


class FakeClaude:
    def __init__(self, result: BrainDumpResult | None):
        self.result = result

    def synthesise(self, raw_text, current_date_line):
        return self.result

    def suggest_efforts(self, summaries):
        return []


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

VALID_SYNTHESIS = """{
  "capture_type": "brain_dump",
  "cleaned_text": "Wedding tasks and an idea about agriculture.",
  "summary": "Wedding tasks + agriculture idea",
  "extracted_tasks": [
    {"title": "Buy dresses", "energy": "medium", "priority": "high", "due": "2026-07-23"}
  ],
  "questions": ["What if farming never happened?"],
  "effort_hints": ["Notes from the Void"]
}"""


class TestParsers:
    def test_plain_json(self):
        result = _parse_synthesis(VALID_SYNTHESIS)
        assert result is not None
        assert result.capture_type == CaptureType.BRAIN_DUMP
        assert result.extracted_tasks[0].title == "Buy dresses"
        assert result.extracted_tasks[0].priority == TaskPriority.HIGH
        assert result.questions == ("What if farming never happened?",)

    def test_fenced_json(self):
        fenced = f"```json\n{VALID_SYNTHESIS}\n```"
        result = _parse_synthesis(fenced)
        assert result is not None
        assert result.summary == "Wedding tasks + agriculture idea"

    def test_fence_without_language(self):
        fenced = f"```\n{VALID_SYNTHESIS}\n```"
        assert _parse_synthesis(fenced) is not None

    def test_invalid_json_returns_none(self):
        assert _parse_synthesis("not json at all") is None

    def test_truncated_json_returns_none(self):
        assert _parse_synthesis(VALID_SYNTHESIS[:80]) is None

    def test_empty_cleaned_text_returns_none(self):
        raw = '{"capture_type": "idea", "cleaned_text": "", "summary": "x"}'
        assert _parse_synthesis(raw) is None

    def test_unknown_enums_fall_back(self):
        raw = """{
          "capture_type": "nonsense",
          "cleaned_text": "text",
          "summary": "s",
          "extracted_tasks": [{"title": "t", "energy": "cosmic", "priority": "urgent"}]
        }"""
        result = _parse_synthesis(raw)
        assert result.capture_type == CaptureType.BRAIN_DUMP
        assert result.extracted_tasks[0].energy == TaskEnergy.MEDIUM
        assert result.extracted_tasks[0].priority == TaskPriority.MEDIUM

    def test_summary_truncated_to_80(self):
        raw = ('{"capture_type": "idea", "cleaned_text": "text", "summary": "'
               + "x" * 200 + '"}')
        assert len(_parse_synthesis(raw).summary) == 80

    def test_effort_suggestions_fenced(self):
        raw = """```json
{"suggestions": [{"title": "Void", "rationale": "keeps coming up", "intensity": "active"}]}
```"""
        result = _parse_effort_suggestions(raw)
        assert len(result) == 1
        assert result[0].title == "Void"

    def test_effort_suggestions_bad_intensity_defaults(self):
        raw = '{"suggestions": [{"title": "X", "rationale": "", "intensity": "blazing"}]}'
        assert _parse_effort_suggestions(raw)[0].intensity == "simmering"

    def test_strip_fences_no_fence_passthrough(self):
        assert _strip_json_fences('{"a": 1}') == '{"a": 1}'


# ---------------------------------------------------------------------------
# Due-hint resolution
# ---------------------------------------------------------------------------

class TestParseLocalDue:
    def test_datetime_gets_local_tz(self):
        due = _parse_local_due("2026-07-21T19:00", TZ)
        assert due.tzinfo is not None
        assert due.astimezone(timezone.utc).hour == 17  # 19:00 Madrid summer = 17:00 UTC

    def test_date_only_defaults_to_nine(self):
        due = _parse_local_due("2026-07-21", TZ)
        assert due.astimezone(TZ).hour == 9

    def test_explicit_offset_respected(self):
        due = _parse_local_due("2026-07-21T17:00+00:00", TZ)
        assert due.astimezone(timezone.utc).hour == 17

    def test_none_and_empty(self):
        assert _parse_local_due(None, TZ) is None
        assert _parse_local_due("", TZ) is None

    def test_garbage_returns_none(self):
        assert _parse_local_due("Thursday 10am", TZ) is None

    def test_midnight_with_time_stays_midnight(self):
        due = _parse_local_due("2026-07-21T00:00", TZ)
        assert due.astimezone(TZ).hour == 0


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

class TestBrainDumpService:
    def _service(self, claude_result):
        return BrainDumpService(FakeCaptureRepo(), FakeTaskRepo(), FakeClaude(claude_result), TZ)

    def test_successful_dump_creates_capture_and_tasks(self):
        result = BrainDumpResult(
            cleaned_text="clean", capture_type=CaptureType.BRAIN_DUMP, summary="s",
            extracted_tasks=(ExtractedTask(title="Buy dresses", due="2026-07-23T10:00"),),
            questions=(), effort_hints=(),
        )
        svc = self._service(result)
        processed = svc.process(UID, "raw text", NOW)
        assert processed.capture.raw == "raw text"
        assert processed.capture.synthesis == "clean"
        assert len(processed.tasks_created) == 1
        assert processed.tasks_created[0].due_at is not None
        assert processed.tasks_created[0].source_capture_id == processed.capture.id

    def test_claude_failure_still_saves_raw(self):
        svc = self._service(None)
        processed = svc.process(UID, "raw text", NOW)
        assert processed.capture.raw == "raw text"
        assert processed.capture.synthesis is None
        assert processed.synthesis is None
        assert processed.tasks_created == ()


class TestTaskService:
    def _service(self):
        return TaskService(FakeTaskRepo(), TZ)

    def test_create_and_complete(self):
        svc = self._service()
        task = svc.create(UID, "Buy wine", now=NOW)
        assert task.status == TaskStatus.OPEN
        done = svc.complete(UID, task.id, now=NOW)
        assert done.status == TaskStatus.DONE
        assert done.completed_at == NOW

    def test_complete_records_event(self):
        repo = FakeTaskRepo()
        svc = TaskService(repo, TZ)
        task = svc.create(UID, "X", now=NOW)
        svc.complete(UID, task.id, now=NOW)
        assert len(repo.events) == 1
        assert repo.events[0].event_type == "completed"

    def test_complete_unknown_task_raises(self):
        with pytest.raises(TaskNotFoundError):
            self._service().complete(UID, uuid4(), now=NOW)

    def test_complete_other_users_task_raises(self):
        svc = self._service()
        task = svc.create(uuid4(), "not yours", now=NOW)
        with pytest.raises(TaskNotFoundError):
            svc.complete(UID, task.id, now=NOW)

    def test_create_with_due(self):
        task = self._service().create(UID, "Fitting", due="2026-07-21T19:00", now=NOW)
        local = task.due_at.astimezone(TZ)
        assert local.weekday() == 1
        assert local.hour == 19


class TestReminderVisibility:
    """Audit item 25: a reminder days out was invisible to focus_get (48h
    window), so the model couldn't look up its id to cancel it."""

    def test_focus_get_lists_far_future_reminders(self):
        from trellis.domain_focus_tool import handle_focus_get
        from trellis.domain_focus_service import CaptureService, EffortService
        svc = ReminderService(FakeReminderRepo(), TZ)
        r = svc.set(UID, "Sunday review", NOW + timedelta(days=5),
                    recurrence="weekly", now=NOW)
        reply = handle_focus_get(
            UID, {"what": "reminders"}, NOW,
            task_service=TaskService(FakeTaskRepo(), TZ),
            goal_service=GoalService(FakeGoalRepo()),
            capture_service=CaptureService(FakeCaptureRepo()),
            effort_service=EffortService(FakeEffortRepo()),
            reminder_service=svc, tz=TZ,
        )
        assert str(r.id) in reply
        assert "repeats weekly" in reply


class TestSetReminderHandler:
    """Timezone math is Python's job — Claude sends local wall-clock time."""

    def _set(self, remind_at_str):
        from trellis.domain_focus_tool import handle_set_reminder
        svc = ReminderService(FakeReminderRepo(), TZ)
        reply = handle_set_reminder(
            UID, {"label": "Fitting", "remind_at": remind_at_str}, NOW,
            reminder_service=svc, tz=TZ,
        )
        reminders = list(svc._repo.reminders.values())
        return reply, reminders

    def test_naive_time_is_local(self):
        _, reminders = self._set("2026-07-21T19:00")
        stored = reminders[0].remind_at
        assert stored.tzinfo is not None
        # 19:00 Madrid summer time == 17:00 UTC
        assert stored.astimezone(timezone.utc).hour == 17

    def test_explicit_utc_respected(self):
        _, reminders = self._set("2026-07-21T17:00Z")
        assert reminders[0].remind_at.astimezone(timezone.utc).hour == 17

    def test_garbage_rejected(self):
        reply, reminders = self._set("next Tuesday-ish")
        assert reminders == []
        assert "Invalid" in reply


class TestReminderService:
    def test_set_and_upcoming(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        svc.set(UID, "Fitting", NOW + timedelta(hours=2), now=NOW)
        svc.set(UID, "Far future", NOW + timedelta(days=10), now=NOW)
        upcoming = svc.upcoming(UID, hours=24, now=NOW)
        assert [r.label for r in upcoming] == ["Fitting"]

    def test_due_now_included_with_zero_window(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        svc.set(UID, "Now", NOW - timedelta(seconds=30), now=NOW - timedelta(minutes=1))
        assert len(svc.upcoming(UID, hours=0, now=NOW)) == 1

    def test_cancel_removes_from_upcoming(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        r = svc.set(UID, "Fitting", NOW + timedelta(hours=1), now=NOW)
        svc.cancel(r.id)
        assert svc.upcoming(UID, hours=24, now=NOW) == []

    def test_mark_sent_removes_from_upcoming(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        r = svc.set(UID, "Fitting", NOW, now=NOW)
        svc.mark_sent(r.id)
        assert svc.upcoming(UID, hours=24, now=NOW) == []

    def test_reschedule_daily_advances_one_day(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        r = svc.set(UID, "Meds", NOW, recurrence="daily", now=NOW)
        next_r = svc.reschedule(UID, r, now=NOW)
        assert next_r.remind_at == NOW + timedelta(days=1)
        assert next_r.recurrence == "daily"

    def test_weekly_reschedules_a_week_on(self):
        svc = ReminderService(FakeReminderRepo(), TZ)
        r = svc.set(UID, "Weekly review", NOW, recurrence="weekly", now=NOW)
        next_r = svc.reschedule(UID, r, now=NOW)
        assert next_r.remind_at == NOW + timedelta(days=7)
        assert next_r.recurrence == "weekly"

    def test_monthly_clamps_short_months(self):
        from datetime import datetime, timezone as _tz
        svc = ReminderService(FakeReminderRepo(), TZ)
        jan31 = datetime(2026, 1, 31, 10, 0, tzinfo=_tz.utc)
        r = svc.set(UID, "Rent", jan31, recurrence="monthly", now=jan31)
        next_r = svc.reschedule(UID, r, now=jan31)
        assert next_r.remind_at == datetime(2026, 2, 28, 10, 0, tzinfo=_tz.utc)

    def test_recurring_catchup_skips_missed_firings(self):
        """After downtime, the next firing is in the FUTURE — no stale backlog."""
        svc = ReminderService(FakeReminderRepo(), TZ)
        three_weeks_ago = NOW - timedelta(days=21)
        r = svc.set(UID, "Weekly review", three_weeks_ago, recurrence="weekly", now=three_weeks_ago)
        next_r = svc.reschedule(UID, r, now=NOW)
        assert next_r.remind_at > NOW
        assert next_r.remind_at <= NOW + timedelta(days=7)


class TestGoalService:
    def test_training_goal_filter(self):
        svc = GoalService(FakeGoalRepo())
        svc.add(UID, "Half marathon", GoalType.RACE, now=NOW)
        svc.add(UID, "Write more", GoalType.LIFE, now=NOW)
        training = svc.list_training_goals(UID)
        assert [g.title for g in training] == ["Half marathon"]
        assert len(svc.list_active(UID)) == 2

    def test_update_unknown_goal_raises(self):
        with pytest.raises(GoalNotFoundError):
            GoalService(FakeGoalRepo()).update(UID, uuid4(), title="x", now=NOW)


# ---------------------------------------------------------------------------
# Model behaviour
# ---------------------------------------------------------------------------

class TestModels:
    def test_task_overdue_only_when_open(self):
        base = Task(id=uuid4(), user_id=UID, title="x", status=TaskStatus.OPEN,
                    priority=TaskPriority.MEDIUM, energy=TaskEnergy.MEDIUM,
                    due_at=NOW - timedelta(hours=1))
        assert base.is_overdue(NOW)
        from dataclasses import replace
        assert not replace(base, status=TaskStatus.DONE).is_overdue(NOW)
        assert not replace(base, due_at=None).is_overdue(NOW)

    def test_goal_is_training_goal(self):
        def goal(gt):
            return Goal(id=uuid4(), user_id=UID, title="g", goal_type=gt)
        assert goal(GoalType.RACE).is_training_goal()
        assert goal(GoalType.AEROBIC).is_training_goal()
        assert goal(GoalType.STRENGTH).is_training_goal()
        assert not goal(GoalType.LIFE).is_training_goal()
        assert not goal(GoalType.GENERAL).is_training_goal()


# ---------------------------------------------------------------------------
# Cross-cutting delete (delete_entry erases tasks/captures/tracking).
# The tracking service itself (SenseService) is tested in test_sense.py.
# ---------------------------------------------------------------------------

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
        from trellis.domain_sense_models import TrackingEventType
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


class TestDeleteEntry:
    """delete_entry is cross-cutting — it erases tasks, captures, OR tracking
    (via sense_service). The state/tracking service tests live in test_sense.py."""

    def test_delete_handler_erases_state(self):
        from trellis.domain_focus_service import CaptureService
        from trellis.domain_focus_tool import handle_delete_entry
        from trellis.domain_sense_service import SenseService
        repo = FakeStateRepo()
        svc = SenseService(repo, TZ)
        task_svc = TaskService(FakeTaskRepo(), TZ)
        log = svc.log_state(UID, "wrong", energy=3, mood=3, now=NOW)
        reply = handle_delete_entry(UID, {"entry_id": str(log.id)}, NOW,
                                    sense_service=svc, task_service=task_svc,
                                    capture_service=CaptureService(FakeCaptureRepo()))
        assert "Erased" in reply
        assert repo.states == []

    def test_delete_handler_erases_duplicate_task(self):
        from trellis.domain_focus_service import CaptureService
        from trellis.domain_focus_tool import handle_delete_entry
        from trellis.domain_sense_service import SenseService
        sense_svc = SenseService(FakeStateRepo(), TZ)
        task_repo = FakeTaskRepo()
        task_svc = TaskService(task_repo, TZ)
        task_svc.create(UID, "Find ceramics class", now=NOW)
        dupe = task_svc.create(UID, "Find a local ceramics class", now=NOW)
        reply = handle_delete_entry(UID, {"entry_id": str(dupe.id)}, NOW,
                                    sense_service=sense_svc, task_service=task_svc,
                                    capture_service=CaptureService(FakeCaptureRepo()))
        assert "Erased" in reply
        assert [t.title for t in task_svc.list_open(UID)] == ["Find ceramics class"]

    def test_delete_handler_erases_capture(self):
        from trellis.domain_focus_service import CaptureService
        from trellis.domain_focus_tool import handle_delete_entry
        from trellis.domain_sense_service import SenseService
        cap_repo = FakeCaptureRepo()
        cap_svc = CaptureService(cap_repo)
        sense_svc = SenseService(FakeStateRepo(), TZ)
        task_svc = TaskService(FakeTaskRepo(), TZ)
        c = cap_repo.save(Capture(id=uuid4(), user_id=UID, raw="test dump",
                                  capture_type=CaptureType.BRAIN_DUMP, synthesis=None,
                                  summary="test", effort_id=None, created_at=NOW))
        reply = handle_delete_entry(UID, {"entry_id": str(c.id)}, NOW,
                                    sense_service=sense_svc, task_service=task_svc,
                                    capture_service=cap_svc)
        assert "Erased" in reply
        assert cap_repo.captures == {}

    def test_delete_other_users_task_refused(self):
        from trellis.domain_focus_service import CaptureService
        from trellis.domain_focus_tool import handle_delete_entry
        from trellis.domain_sense_service import SenseService
        sense_svc = SenseService(FakeStateRepo(), TZ)
        task_repo = FakeTaskRepo()
        task_svc = TaskService(task_repo, TZ)
        other = task_svc.create(uuid4(), "not yours", now=NOW)
        reply = handle_delete_entry(UID, {"entry_id": str(other.id)}, NOW,
                                    sense_service=sense_svc, task_service=task_svc,
                                    capture_service=CaptureService(FakeCaptureRepo()))
        assert "No record" in reply
        assert len(task_repo.tasks) == 1


class TestSeedsAndParked:
    def _service(self, repo=None):
        return TaskService(repo or FakeTaskRepo(), TZ)

    def test_seed_never_gets_due_date(self):
        from trellis.domain_focus_models import TaskKind
        svc = self._service()
        seed = svc.create(UID, "Look into ceramics", kind=TaskKind.SEED,
                          due="2026-07-25", now=NOW)
        assert seed.due_at is None
        assert seed.kind == TaskKind.SEED

    def test_list_open_excludes_seeds(self):
        from trellis.domain_focus_models import TaskKind
        svc = self._service()
        svc.create(UID, "Buy wine", now=NOW)
        svc.create(UID, "Research drum machines", kind=TaskKind.SEED, now=NOW)
        assert [t.title for t in svc.list_open(UID)] == ["Buy wine"]
        assert [t.title for t in svc.list_seeds(UID)] == ["Research drum machines"]

    def test_park_and_reclaim(self):
        from trellis.domain_focus_models import TaskStatus
        repo = FakeTaskRepo()
        svc = self._service(repo)
        task = svc.create(UID, "Sort shoes", now=NOW)
        svc.update(UID, task.id, status=TaskStatus.PARKED, now=NOW)
        assert svc.list_open(UID) == []
        assert [t.title for t in svc.list_parked(UID)] == ["Sort shoes"]
        svc.update(UID, task.id, status=TaskStatus.OPEN, now=NOW)
        assert [t.title for t in svc.list_open(UID)] == ["Sort shoes"]

    def test_reclassify_todo_to_seed(self):
        from trellis.domain_focus_models import TaskKind
        svc = self._service()
        task = svc.create(UID, "Research flying", now=NOW)
        updated = svc.update(UID, task.id, kind=TaskKind.SEED, now=NOW)
        assert updated.kind == TaskKind.SEED
        assert svc.list_open(UID) == []

    def test_synthesis_kind_parsed(self):
        raw = """{
          "capture_type": "brain_dump", "cleaned_text": "text", "summary": "s",
          "extracted_tasks": [
            {"title": "Buy wine", "kind": "todo"},
            {"title": "Look into ceramics", "kind": "seed"}
          ]
        }"""
        result = _parse_synthesis(raw)
        from trellis.domain_focus_models import TaskKind
        assert result.extracted_tasks[0].kind == TaskKind.TODO
        assert result.extracted_tasks[1].kind == TaskKind.SEED


class TestWebSearch:
    def _tools(self, web_search):
        from trellis.domain_focus_tool import focus_tools
        svc = None
        return focus_tools(
            task_service=svc, goal_service=svc, capture_service=svc,
            effort_service=svc, reminder_service=svc, cleanup_service=svc,
            sense_service=svc, web_search=web_search, tz=TZ,
        )

    def test_tool_absent_when_no_provider(self):
        names = [s["name"] for s, _ in self._tools(None)]
        assert "web_search" not in names

    def test_tool_present_when_provider(self):
        class FakeSearch:
            def search(self, q, *, max_results=5): return None
        names = [s["name"] for s, _ in self._tools(FakeSearch())]
        assert "web_search" in names

    def test_handler_formats_results(self):
        from trellis.domain_focus_tool import handle_web_search
        from trellis.infra_search import SearchResponse, SearchResult
        class FakeSearch:
            def search(self, q, *, max_results=5):
                return SearchResponse(
                    query=q, answer="Short answer.",
                    results=(SearchResult("Title A", "https://a.com", "snippet a"),),
                )
        reply = handle_web_search(UID, {"query": "drum machines"}, NOW, web_search=FakeSearch())
        assert "Short answer." in reply
        assert "https://a.com" in reply

    def test_handler_empty(self):
        from trellis.domain_focus_tool import handle_web_search
        class FakeSearch:
            def search(self, q, *, max_results=5): return None
        reply = handle_web_search(UID, {"query": "x"}, NOW, web_search=FakeSearch())
        assert "empty" in reply or "unavailable" in reply

    def test_handler_requires_query(self):
        from trellis.domain_focus_tool import handle_web_search
        reply = handle_web_search(UID, {}, NOW, web_search=object())
        assert "required" in reply


class TestSaveToEffort:
    def _wire(self):
        from trellis.domain_focus_service import CaptureService, EffortService
        cap_repo = FakeCaptureRepo()
        eff_repo = FakeEffortRepo()
        task_repo = FakeTaskRepo()
        return (
            CaptureService(cap_repo), EffortService(eff_repo),
            TaskService(task_repo, TZ), cap_repo, eff_repo, task_repo,
        )

    def test_creates_effort_and_saves_research(self):
        from trellis.domain_focus_tool import handle_save_to_effort
        cap_svc, eff_svc, task_svc, cap_repo, eff_repo, _ = self._wire()
        reply = handle_save_to_effort(
            UID, {"effort_title": "Making Music", "content": "PO-33 ~£90\nCircuit Tracks ~£300"},
            NOW, effort_service=eff_svc, capture_service=cap_svc, task_service=task_svc,
        )
        assert "Making Music" in reply
        assert len(eff_repo.efforts) == 1
        cap = list(cap_repo.captures.values())[0]
        assert cap.effort_id == list(eff_repo.efforts.values())[0].id
        assert "PO-33" in cap.raw

    def test_reuses_existing_effort_by_name(self):
        from trellis.domain_focus_tool import handle_save_to_effort
        cap_svc, eff_svc, task_svc, cap_repo, eff_repo, _ = self._wire()
        for content in ("first find", "second find"):
            handle_save_to_effort(UID, {"effort_title": "Making Music", "content": content},
                                  NOW, effort_service=eff_svc, capture_service=cap_svc, task_service=task_svc)
        assert len(eff_repo.efforts) == 1          # not duplicated
        assert len(cap_repo.captures) == 2         # both findings kept

    def test_graduated_seed_retired(self):
        from trellis.domain_focus_tool import handle_save_to_effort
        from trellis.domain_focus_models import TaskKind, TaskStatus
        cap_svc, eff_svc, task_svc, cap_repo, eff_repo, task_repo = self._wire()
        seed = task_svc.create(UID, "look into drum machines", kind=TaskKind.SEED, now=NOW)
        handle_save_to_effort(
            UID, {"effort_title": "Making Music", "content": "options...",
                  "graduated_seed_id": str(seed.id)},
            NOW, effort_service=eff_svc, capture_service=cap_svc, task_service=task_svc,
        )
        assert task_repo.tasks[seed.id].status == TaskStatus.DROPPED
        assert task_svc.list_seeds(UID) == []
