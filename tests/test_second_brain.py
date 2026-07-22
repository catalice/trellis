"""Unit tests for the second brain domain — parsers, services, models.

Uses in-memory fake repos; no DB, no API calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trellis.domain_second_brain_claude import (
    _parse_effort_suggestions,
    _parse_synthesis,
    _strip_json_fences,
)
from trellis.domain_second_brain_models import (
    BrainDumpResult,
    Capture,
    CaptureType,
    CleanupAssignment,
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
from trellis.domain_second_brain_service import (
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
                if t.user_id == user_id and t.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)]

    def list_recent(self, user_id, *, limit):
        return list(self.tasks.values())[:limit]

    def update(self, task_id, **kwargs):
        from dataclasses import replace
        task = self.tasks[task_id]
        updated = replace(task, **kwargs)
        self.tasks[task_id] = updated
        return updated

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

    def test_overdue(self):
        svc = self._service()
        svc.create(UID, "no deadline", now=NOW - timedelta(days=3))
        svc.create(UID, "was due", due="2026-07-19T09:00", now=NOW - timedelta(days=2))
        assert [t.title for t in svc.overdue(UID, NOW)] == ["was due"]


class TestSetReminderHandler:
    """Timezone math is Python's job — Claude sends local wall-clock time."""

    def _set(self, remind_at_str):
        from trellis.domain_second_brain_tool import handle_set_reminder
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
        r = svc.set(UID, "Meds", NOW, recur_daily=True, now=NOW)
        next_r = svc.reschedule_daily(UID, r, now=NOW)
        assert next_r.remind_at == NOW + timedelta(days=1)
        assert next_r.recur_daily is True


class TestGoalService:
    def test_training_goal_filter(self):
        svc = GoalService(FakeGoalRepo())
        svc.add(UID, "Half marathon", GoalType.RACE, now=NOW)
        svc.add(UID, "Write more", GoalType.LIFE, now=NOW)
        training = svc.list_training_goals(UID)
        assert [g.title for g in training] == ["Half marathon"]
        assert len(svc.list_active(UID)) == 2

    def test_achieve(self):
        svc = GoalService(FakeGoalRepo())
        g = svc.add(UID, "X", GoalType.HABIT, now=NOW)
        achieved = svc.achieve(UID, g.id, now=NOW)
        assert achieved.status == GoalStatus.ACHIEVED
        assert svc.list_active(UID) == []

    def test_update_unknown_goal_raises(self):
        with pytest.raises(GoalNotFoundError):
            GoalService(FakeGoalRepo()).update(UID, uuid4(), title="x", now=NOW)


class TestCleanupService:
    def test_apply_counts(self):
        captures = FakeCaptureRepo()
        efforts = FakeEffortRepo()
        svc = CleanupService(captures, efforts, FakeClaude(None))
        c1 = captures.save(Capture(id=uuid4(), user_id=UID, raw="a",
                                   capture_type=CaptureType.IDEA, synthesis=None,
                                   summary="a", effort_id=None, created_at=NOW))
        c2 = captures.save(Capture(id=uuid4(), user_id=UID, raw="b",
                                   capture_type=CaptureType.IDEA, synthesis=None,
                                   summary="b", effort_id=None, created_at=NOW))
        eid = uuid4()
        summary = svc.apply(UID, [
            CleanupAssignment(capture_id=c1.id, effort_id=eid, action="assigned"),
            CleanupAssignment(capture_id=c2.id, effort_id=None, action="archived"),
        ])
        assert summary.assigned == 1
        assert summary.archived == 1
        assert captures.captures[c1.id].effort_id == eid
        assert c2.id in captures.archived


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
# Self-tracking (StateService + log_state handler)
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
            (s for s in self.states if s.logged_at >= since),
            key=lambda s: s.logged_at,
        )

    def list_events_since(self, user_id, *, since):
        return [e for e in self.events if e.occurred_at >= since]

    def last_period_start(self, user_id):
        from trellis.domain_second_brain_models import TrackingEventType
        starts = [e for e in self.events if e.event_type == TrackingEventType.PERIOD_START]
        return max(starts, key=lambda e: e.occurred_at) if starts else None


class TestStateService:
    def _service(self, repo=None):
        from trellis.domain_second_brain_service import StateService
        return StateService(repo or FakeStateRepo(), TZ)

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
        from trellis.domain_second_brain_models import TrackingEventType
        repo = FakeStateRepo()
        svc = self._service(repo)
        svc.log_event(UID, TrackingEventType.PERIOD_START,
                      occurred_at=NOW - timedelta(days=3))
        assert svc.cycle_day(UID, NOW) == 4

    def test_cycle_day_none_without_period(self):
        assert self._service().cycle_day(UID, NOW) is None

    def test_cycle_day_none_when_stale(self):
        from trellis.domain_second_brain_models import TrackingEventType
        repo = FakeStateRepo()
        svc = self._service(repo)
        svc.log_event(UID, TrackingEventType.PERIOD_START,
                      occurred_at=NOW - timedelta(days=90))
        assert svc.cycle_day(UID, NOW) is None


class TestLogStateHandler:
    def _handle(self, input_dict, repo=None):
        from trellis.domain_second_brain_service import StateService
        from trellis.domain_second_brain_tool import handle_log_state
        repo = repo or FakeStateRepo()
        svc = StateService(repo, TZ)
        reply = handle_log_state(UID, input_dict, NOW, state_service=svc, tz=TZ)
        return reply, repo

    def test_full_checkin(self):
        from trellis.domain_second_brain_models import TrackingEventType
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
        from trellis.domain_second_brain_models import TrackingEventType
        reply, repo = self._handle({"note": "period started", "period": "started"})
        assert [e.event_type for e in repo.events] == [TrackingEventType.PERIOD_START]

    def test_note_required(self):
        reply, repo = self._handle({"energy": 3})
        assert repo.states == []
        assert "note is required" in reply

    def test_bad_med_time_still_logs_med(self):
        from trellis.domain_second_brain_models import TrackingEventType
        reply, repo = self._handle({
            "note": "took meds", "meds": [{"name": "dex", "time": "nineish"}],
        })
        meds = [e for e in repo.events if e.event_type == TrackingEventType.MEDS]
        assert len(meds) == 1
        assert meds[0].occurred_at == NOW
