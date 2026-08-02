"""Unit tests for core pieces: router defaults, oracle tool trace, obsidian vault."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.core_oracle import OracleResult, ToolCall
from trellis.core_router import Router
from trellis.domain_focus_models import (
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
    SIGNALS = {"focus": ["task", "remind"], "move": ["run", "garmin"]}

    def test_matches_signal(self):
        r = Router(self.SIGNALS, default_domain="focus")
        assert r.route("how was my run") == {"move"}

    def test_multiple_domains(self):
        r = Router(self.SIGNALS, default_domain="focus")
        assert r.route("task list before my run") == {"focus", "move"}

    def test_unmatched_goes_to_default(self):
        r = Router(self.SIGNALS, default_domain="focus")
        assert r.route("hello there") == {"focus"}

    def test_reminder_variants_route(self):
        r = Router(self.SIGNALS, default_domain="focus")
        assert r.route("could you add a reminder please") == {"focus"}
        assert r.route("remind me tomorrow") == {"focus"}

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


class TestOracleSilentFinish:
    """The 2 Aug 'Something went wrong' bug: the model called tools (which
    succeeded — task dropped, effort updated) then ended its turn with no text.
    Empty text after successful tool calls must fall back to the tool results,
    never surface as a failure."""

    class _Resp:
        def __init__(self, stop_reason, content):
            self.stop_reason = stop_reason
            self.content = content

    class _Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def _oracle(self, responses):
        from trellis.core_oracle import Oracle

        class FakeMessages:
            def __init__(self, resps): self._resps = list(resps)
            def create(self, **kwargs): return self._resps.pop(0)

        class FakeClient:
            def __init__(self, resps): self.messages = FakeMessages(resps)

        return Oracle(client=FakeClient(responses), model="test")

    def test_silent_end_turn_replies_with_tool_results(self):
        tool_use = self._Block(type="tool_use", name="save_to_effort", id="t1",
                               input={"text": "cabinet door"})
        oracle = self._oracle([
            self._Resp("tool_use", [tool_use]),
            self._Resp("end_turn", []),          # model goes silent
        ])
        result = oracle.run("sys", [{"role": "user", "content": "hi"}],
                            tools=[{"name": "save_to_effort"}],
                            handlers={"save_to_effort": lambda inp: "Saved to effort 'Dining Area Upgrade'."})
        assert result.text == "Saved to effort 'Dining Area Upgrade'."
        assert result.tool_calls[0].name == "save_to_effort"

    def test_text_reply_unchanged(self):
        oracle = self._oracle([
            self._Resp("end_turn", [self._Block(type="text", text="All done!")]),
        ])
        result = oracle.run("sys", [{"role": "user", "content": "hi"}], tools=[], handlers={})
        assert result.text == "All done!"


class TestPreferencesLoading:
    """Preferences were written but never read (the 'saved under learn' black
    hole). Now: global preferences load every turn; domain preferences load with
    their routed domain."""

    class _FakePrefs:
        def __init__(self, data): self._data = data
        def get(self, user_id, domain): return self._data.get(domain)

    class _FakeHistory:
        def domain_summary(self, user_id, domain): return None

    def _assembler(self, prefs):
        from trellis.core_assembler import Assembler
        from trellis.core_registry import TrellisRegistry
        registry = TrellisRegistry()
        registry.add_domain("move", lambda uid, now: None, [], ["run"])
        return Assembler(
            oracle=None, registry=registry, history=self._FakeHistory(),
            permanent=[], always_tools=[],
            preferences=self._FakePrefs(prefs),
        )

    def test_global_prefs_load_every_turn(self):
        a = self._assembler({"global": "You never use tables."})
        ctx = a._build_context(UID, NOW, domains=set())
        assert "You never use tables." in ctx
        assert "always apply" in ctx

    def test_domain_prefs_load_when_routed(self):
        a = self._assembler({"move": "You prefer shorter sessions."})
        assert "shorter sessions" in a._build_context(UID, NOW, domains={"move"})
        assert "shorter sessions" not in a._build_context(UID, NOW, domains=set())

    def test_no_prefs_no_crash(self):
        a = self._assembler({})
        assert a._build_context(UID, NOW, domains={"move"}) is not None


class TestVaultTrainingPlan:
    """Training/Plan.md — the coach's plan made inspectable, with completed
    runs ticked off against planned sessions."""

    class _MoveRepo:
        def __init__(self, plan, runs): self._plan, self._runs = plan, runs
        def get(self, user_id): return self._plan
        def recent_runs(self, user_id, *, limit): return self._runs[:limit]

    def test_plan_page_matches_runs(self, tmp_path):
        from datetime import date as d
        from trellis.domain_move_models import RunLog, TrainingPlan
        plan = TrainingPlan(
            user_id=UID, goal_id=None, baseline="28min run-walk base",
            plan={"arc": "Base phase, 6 weeks to 5k.",
                  "week": [
                      {"date": "2026-08-02", "type": "easy", "detail": "28min 3:1 run-walk"},
                      {"date": "2026-08-04", "type": "long", "detail": "40min easy"},
                  ]},
        )
        runs = [RunLog(id=uuid4(), user_id=UID, ran_on=d(2026, 8, 2),
                       note="Barcelona Running (avg HR 152)", distance_km=3.26),
                RunLog(id=uuid4(), user_id=UID, ran_on=d(2026, 7, 30),
                       note="Drill workout", distance_km=6.38)]
        vault = ObsidianVault(tmp_path, TZ, None, None, None,
                              move_repo=self._MoveRepo(plan, runs))
        vault.plan_changed(UID)
        page = (tmp_path / "Calendar" / "Training" / "Training.md").read_text()
        assert "Base phase" in page and "28min run-walk base" in page
        assert "- [x] Sun 02 Aug — easy: 28min 3:1 run-walk — 3.26km done" in page
        assert "- [ ] Tue 04 Aug — long: 40min easy" in page
        assert "Drill workout" in page  # unmatched run listed under Recent Runs
        week_file = tmp_path / "Calendar" / "Training" / "Weeks" / "2026-W31.md"
        assert "- [x] Sun 02 Aug" in week_file.read_text()  # archived, ticked

    def test_no_plan_no_crash(self, tmp_path):
        vault = ObsidianVault(tmp_path, TZ, None, None, None,
                              move_repo=self._MoveRepo(None, []))
        vault.plan_changed(UID)
        assert "No plan yet" in (tmp_path / "Calendar" / "Training" / "Training.md").read_text()


class TestVaultDailyProperties:
    """Daily notes carry frontmatter properties so a Base can table the whole
    tracking history. Body stays append-only; only the metadata block rewrites."""

    class _StateRepo:
        def __init__(self, states, events, period_start=None):
            self._s, self._e, self._p = states, events, period_start
        def list_states_since(self, user_id, *, since): return self._s
        def list_events_since(self, user_id, *, since): return self._e
        def last_period_start(self, user_id): return self._p

    def test_state_log_stamps_frontmatter(self, tmp_path):
        from trellis.domain_sense_models import StateLog
        log = StateLog(id=uuid4(), user_id=UID, note="feeling okay",
                       energy=3, mood=4, felt_at=NOW, logged_at=NOW)
        vault = ObsidianVault(tmp_path, TZ, None, None, None,
                              state_repo=self._StateRepo([log], []))
        vault.state_logged(log)
        note = (tmp_path / "Calendar" / "Captures" / "2026-07-20.md").read_text()
        assert note.startswith("---\n")
        assert "energy: 3" in note and "mood: 4" in note
        assert "tracking e3/m4" in note  # receipt body preserved below

    def test_frontmatter_rewrites_body_survives(self, tmp_path):
        from trellis.domain_sense_models import StateLog
        log1 = StateLog(id=uuid4(), user_id=UID, note="meh", energy=2, mood=2,
                        felt_at=NOW, logged_at=NOW)
        log2 = StateLog(id=uuid4(), user_id=UID, note="better", energy=4, mood=4,
                        felt_at=NOW + timedelta(hours=3), logged_at=NOW + timedelta(hours=3))
        repo = self._StateRepo([log1, log2], [])
        vault = ObsidianVault(tmp_path, TZ, None, None, None, state_repo=repo)
        vault.state_logged(log1)
        vault.state_logged(log2)
        note = (tmp_path / "Calendar" / "Captures" / "2026-07-20.md").read_text()
        assert note.count("---\n") == 2          # exactly one frontmatter block
        assert "energy: 2\u20134" in note          # RANGE, not a flattened average
        assert "entries: 2" in note
        assert note.count("tracking") == 2       # both receipts kept
