"""Stage 3: what the person is told follows what actually happened.

These are SOFTWARE guarantees, so they run against scripted models that behave
badly on purpose: claiming success before a tool fails, claiming it afterwards,
going silent, retrying blind. The checks read the recorded outcome and the
receipt the engine adds — never a word match on the model's own text."""
from __future__ import annotations

import pytest

from harness import Scenario, Step, run, tool
from trellis.core_actions import Status, done, failed, partial, refused

SAVE_NOTE = tool("save_note", "Save a note.", text="The note.")
SAVE = (("save_note", {"text": "the boiler needs servicing"}),)


def _scenario(script, behaviour, name="x"):
    return run(Scenario(name=name, message="Save a note that the boiler needs servicing.",
                        tools={"save_note": (SAVE_NOTE, behaviour)}, script=script, checks=[]))


def _statuses(outcome):
    return [a.status for a in outcome.result.actions]


class TestAFailedActionIsNeverDeliveredAsSuccess:
    """When something did not cleanly succeed, the model's draft is not shipped:
    it is handed back with the record and rewritten once. What ships is that
    rewrite and then the record's own statement — never the draft's claims."""

    def test_success_claimed_before_the_tool_failed_then_silence(self):
        """The reviewed reproduction: 'Done — saved.', the save fails, the model says nothing more."""
        o = _scenario([Step(text="Done — saved.", tools=SAVE), Step(text=""),
                       Step(text="I tried to save that note, but it didn't go through.")],
                      failed("Save failed; nothing changed."))
        assert _statuses(o) == [Status.FAILED]
        assert "Done — saved." not in o.reply                           # the false claim never ships
        assert o.reply.startswith("I tried to save that note")
        assert o.reply.rstrip().endswith("Not done: Save failed; nothing changed.")

    def test_success_claimed_after_the_tool_failed(self):
        o = _scenario([Step(tools=SAVE), Step(text="Saved your note. There were no failures."),
                       Step(text="That note didn't save.")],
                      failed("Save failed; nothing changed."))
        assert "Saved your note" not in o.reply and "no failures" not in o.reply
        assert o.reply.rstrip().endswith("Not done: Save failed; nothing changed.")

    def test_the_rewrite_is_given_the_draft_and_the_record(self):
        o = _scenario([Step(text="12 times 12 is 144. Done — saved.", tools=SAVE), Step(text="All saved."),
                       Step(text="12 times 12 is 144. The note didn't save.")],
                      failed("Save failed; nothing changed."))
        asked = o.model.said[-1]
        assert "12 times 12 is 144" in asked and "Save failed; nothing changed." in asked
        assert o.reply.startswith("12 times 12 is 144.")                # the substantive answer survives

    def test_a_model_that_will_not_rewrite_gets_only_the_record(self):
        """If the rewrite comes back empty, nothing the draft claimed is shipped at all."""
        o = _scenario([Step(text="Done — saved.", tools=SAVE), Step(text="Saved."), Step(text="")],
                      failed("Save failed; nothing changed."))
        assert "saved" not in o.reply.lower().replace("save failed", "")
        assert "Not done: Save failed; nothing changed." in o.reply

    def test_a_clean_success_adds_nothing_and_costs_no_extra_call(self):
        o = _scenario([Step(tools=SAVE), Step(text="Saved your note.")], done("Saved."))
        assert _statuses(o) == [Status.SUCCEEDED] and o.reply == "Saved your note."
        assert o.model.said == [] and o.model.steps_left == 0

    def test_partial_success_is_said_as_partial(self):
        o = _scenario([Step(tools=SAVE), Step(text="All done."), Step(text="Saved the note; its page didn't update.")],
                      partial("Saved the note; the vault page didn't update."))
        assert _statuses(o) == [Status.PARTIAL] and "Partly done" in o.reply and "All done." not in o.reply


class TestWhatATellsUsNothingIsNotASuccess:
    """A tool that changes things must SAY it succeeded. A plain string from one
    is a refusal or a validation message until proven otherwise — it used to be
    recorded as 'succeeded', and 'Removed that preference.' shipped after
    'That rule_id isn't a valid id.'"""

    def test_an_undeclared_result_from_a_changing_tool_is_not_done(self):
        o = _scenario([Step(tools=SAVE), Step(text="Removed that preference."), Step(text="That id wasn't valid.")],
                      "That rule_id isn't a valid id.")
        assert _statuses(o) == [Status.FAILED]
        assert "Removed that preference." not in o.reply
        assert "Not done: That rule_id isn't a valid id." in o.reply

    def test_a_read_only_tool_may_answer_in_plain_text(self):
        from harness import Scenario, run as run_scenario
        look = tool("look_up", "Read something.", what="What.")
        o = run_scenario(Scenario(name="x", message="what's stored?", tools={"look_up": (look, "Nothing stored yet.")},
                                  script=[Step(tools=(("look_up", {"what": "notes"}),)), Step(text="Nothing yet.")],
                                  checks=[]), read_only={"look_up"})
        assert _statuses(o) == [Status.SUCCEEDED] and o.reply == "Nothing yet."


class TestOneSuccessNeverHidesAnotherFailure:
    def test_two_different_notes_one_failed_one_saved(self):
        """The reviewed reproduction: the delivered reply was 'Both notes saved.'"""
        attempts = iter([failed("Save failed; nothing changed."), done("Saved.")])
        o = _scenario([Step(tools=SAVE), Step(tools=(("save_note", {"text": "the quince needs planting"}),)),
                       Step(text="Both notes saved."), Step(text="The quince note saved; the boiler one didn't.")],
                      lambda args: next(attempts))
        assert _statuses(o) == [Status.FAILED, Status.SUCCEEDED]
        assert "Both notes saved." not in o.reply
        assert "Not done: Save failed; nothing changed." in o.reply

    def test_a_refusal_resolved_by_the_same_request_reworded_is_not_warned_about(self):
        long_rule = "please always make sure you never ever offer me menus of options"
        attempts = iter([refused("Not saved — 13 words, the limit is 10."), done("Rule saved (global).")])
        o = _scenario([Step(tools=(("save_note", {"text": long_rule}),)),
                       Step(tools=(("save_note", {"text": "never offer me menus of options"}),)),
                       Step(text="Saved it.")], lambda args: next(attempts))
        assert _statuses(o) == [Status.FAILED, Status.SUCCEEDED]      # both on the record
        assert o.reply == "Saved it."                                 # the person got what they asked for

    def test_a_refusal_followed_by_a_different_request_is_still_warned_about(self):
        attempts = iter([refused("Not saved — 13 words, the limit is 10."), done("Rule saved (global).")])
        o = _scenario([Step(tools=(("save_note", {"text": "please always make sure you never ever offer me menus"}),)),
                       Step(tools=(("save_note", {"text": "order my task list by priority"}),)),
                       Step(text="Saved both."), Step(text="Saved the task-order rule; the menus rule was too long.")],
                      lambda args: next(attempts))
        assert "Not done: Not saved — 13 words" in o.reply


class TestAnUnknownOutcomeStaysUnknown:
    @staticmethod
    def _changes_then_times_out(stored):
        def behaviour(args):
            stored.append(args["text"])
            raise TimeoutError("no acknowledgement")
        return behaviour

    def test_an_action_that_raised_is_unknown_not_failed(self):
        stored = []
        o = _scenario([Step(tools=SAVE), Step(text="Saved.")], self._changes_then_times_out(stored))
        assert stored and _statuses(o) == [Status.UNKNOWN]
        assert "Not confirmed" in o.reply
        told = o.model.results_given[0][0].content
        assert "unknown" in told.lower() and "try again" not in told.lower()     # no invitation to retry blind

    def test_a_blind_retry_of_an_unknown_action_is_refused(self):
        stored = []
        o = _scenario([Step(tools=SAVE), Step(tools=SAVE), Step(text="Saved.")],
                      self._changes_then_times_out(stored))
        assert stored == ["the boiler needs servicing"]                          # ran once, not twice
        assert "not retried" in o.model.results_given[1][0].content.lower()
        assert _statuses(o) == [Status.UNKNOWN]                                  # the refusal is not an action

    def test_a_different_request_after_an_unknown_one_still_runs(self):
        stored = []
        other = (("save_note", {"text": "something else"}),)
        _scenario([Step(tools=SAVE), Step(tools=other), Step(text="ok")], self._changes_then_times_out(stored))
        assert stored == ["the boiler needs servicing", "something else"]


class TestTheActionRecordOutlivesTheModel:
    def test_every_attempt_is_recorded_before_it_runs_and_closed_after(self):
        from trellis.core_actions import InMemoryActionLog
        from trellis.core_oracle import Oracle
        from harness import ScriptedModel, SimulatedTools
        log = InMemoryActionLog()
        tools = SimulatedTools({"save_note": (SAVE_NOTE, failed("Save failed; nothing changed."))})
        Oracle(ScriptedModel([Step(tools=SAVE), Step(text="x")])).run(
            "sys", [{"role": "user", "content": "q"}], tools.schemas, tools.handlers, actions=log)
        (entry,) = log.entries
        assert (entry.tool, entry.status, entry.input) == ("save_note", Status.FAILED, {"text": "the boiler needs servicing"})

    def test_a_model_that_dies_mid_turn_leaves_the_record_of_what_was_done(self):
        from trellis.core_actions import InMemoryActionLog
        from trellis.core_oracle import Oracle
        from harness import ScriptedModel, SimulatedTools
        log = InMemoryActionLog()
        tools = SimulatedTools({"save_note": (SAVE_NOTE, done("Saved."))})
        model = ScriptedModel([Step(tools=SAVE)])             # asks for the tool, then has no more steps: dies
        with pytest.raises(AssertionError):
            Oracle(model).run("sys", [{"role": "user", "content": "q"}], tools.schemas, tools.handlers, actions=log)
        assert [(e.tool, e.status) for e in log.entries] == [("save_note", Status.SUCCEEDED)]

    def test_an_attempt_the_process_never_closed_reads_as_unknown(self):
        from trellis.core_actions import InMemoryActionLog
        log = InMemoryActionLog()
        log.begin("save_note", {"text": "x"})                  # ...and the process dies here
        assert log.entries[0].status is Status.UNKNOWN


class TestATurnThatDiesSaysWhatItHadDone:
    """History used to get a generic 'actions may have completed' line. It now
    gets the record."""

    def _assembler(self, model, history, tools):
        from trellis.core_actions import InMemoryActionLog
        from trellis.core_assembler import Assembler
        from trellis.core_oracle import Oracle
        from trellis.core_registry import TrellisRegistry
        self.log = InMemoryActionLog()
        return Assembler(
            oracle=Oracle(model), registry=TrellisRegistry(), history=history, permanent=[],
            always_tools=[(schema, lambda uid, inp, now, h=handler: h(inp))
                          for schema, handler in zip(tools.schemas, tools.handlers.values())],
            action_log=lambda uid: self.log,
        )

    class _History:
        def __init__(self): self.rows = []
        def append(self, user_id, role, content, metadata=None): self.rows.append((role, content))
        def recent_window(self, user_id, *, since, cap): return []
        def to_messages(self, turns): return []
        def domain_summary(self, user_id, domain): return None
        def turn_count(self, user_id): return 0
        def max_turns_covered(self, user_id): return 0

    def test_history_records_the_action_that_completed_before_the_model_died(self):
        from uuid import uuid4
        from harness import ScriptedModel, SimulatedTools
        history = self._History()
        tools = SimulatedTools({"save_note": (SAVE_NOTE, done("Saved."))})
        assembler = self._assembler(ScriptedModel([Step(tools=SAVE)]), history, tools)   # then no more steps: it dies
        with pytest.raises(AssertionError):
            assembler.handle_turn(uuid4(), "Save a note that the boiler needs servicing.")
        role, line = history.rows[-1]
        assert role == "assistant" and "save_note → SUCCEEDED: Saved." in line


class TestAnUnknownOutcomeIsNeverCleared:
    def test_a_later_success_does_not_clear_it(self):
        def behaviour(args):
            if args["text"] == "the boiler needs servicing":
                raise TimeoutError()
            return done("Saved.")
        o = _scenario([Step(tools=SAVE), Step(tools=(("save_note", {"text": "other"}),)), Step(text="Done."),
                       Step(text="One saved; I can't confirm the other.")], behaviour)
        assert "Not confirmed" in o.reply


class TestNoRecordNoAction:
    """A changing action starts only once its attempt is durably on record. If
    the record can't be written, the action does not run — a crash afterwards
    would otherwise leave a change nobody can account for."""

    class _LogDown:
        def begin(self, tool, input): return None            # could not be written
        def finish(self, handle, status, summary): raise AssertionError("nothing to finish")

    def _run(self, read_only=frozenset()):
        from harness import ScriptedModel, SimulatedTools
        from trellis.core_oracle import Oracle
        changed = []
        tools = SimulatedTools({"save_note": (SAVE_NOTE, lambda args: changed.append(args) or done("Saved."))})
        model = ScriptedModel([Step(tools=SAVE), Step(text="Saved."), Step(text="I couldn't do that just now.")])
        result = Oracle(model).run("sys", [{"role": "user", "content": "q"}], tools.schemas, tools.handlers,
                                   actions=self._LogDown(), read_only=read_only)
        return changed, result

    def test_a_changing_action_does_not_run_without_its_record(self):
        changed, result = self._run()
        assert changed == []
        assert [a.status for a in result.actions] == [Status.FAILED]
        assert "Saved." not in result.text and "Not done" in result.text

    def test_a_read_only_tool_still_runs(self):
        changed, _ = self._run(read_only=frozenset({"save_note"}))
        assert len(changed) == 1


class TestHandlersSayHowItWent:
    """The status rides the handler's own text — checked on the real handlers."""

    def test_a_rule_refused_for_length_is_a_failure(self):
        from datetime import datetime, timezone
        from uuid import uuid4
        from trellis.core_actions import status_of
        from trellis.core_meta_tool import handle_save_preferences
        from trellis.core_profile import LineGuard
        out = handle_save_preferences(
            uuid4(), {"action": "add", "text": "one two three four five six seven eight nine ten eleven"},
            datetime.now(timezone.utc), preferences_repository=object(), guard=LineGuard())
        assert status_of(out) is Status.FAILED

    def test_a_save_that_raises_part_way_is_unknown_and_invites_no_retry(self):
        from datetime import datetime, timezone
        from uuid import uuid4
        from trellis.core_actions import status_of
        from trellis.core_meta_tool import handle_save_preferences

        class Breaks:
            def list_rules(self, uid): return []
            def add_rule(self, uid, domain, text): raise ConnectionError("lost mid-write")

        out = handle_save_preferences(uuid4(), {"action": "add", "text": "short rule"},
                                      datetime.now(timezone.utc), preferences_repository=Breaks())
        assert status_of(out) is Status.UNKNOWN and "try again" not in out.lower()

    def test_a_partial_garmin_sync_is_partial(self):
        from datetime import datetime, timezone
        from uuid import uuid4
        from trellis.core_actions import status_of
        from trellis.domain_move_tool import handle_sync_garmin

        class Move:
            def sync_garmin(self, user_id, *, now):
                return {"activities": 1, "health_records": 1, "health_through": "2026-03-10",
                        "unavailable": {"2026-03-10": ("sleep",)}}

        assert status_of(handle_sync_garmin(uuid4(), {}, datetime.now(timezone.utc), move_service=Move())) is Status.PARTIAL


class TestEveryWriteHandlerDeclaresItsOutcome:
    """A new `return "…"` in a handler that changes things would read as 'not
    done' at runtime (safe, but wrong). This catches it at the desk instead: in
    these functions every return is a call — done/refused/failed/partial/unknown,
    or a hand-off to another handler."""

    WRITE_HANDLERS = {
        "core_meta_tool.py": {"handle_update_current_context", "handle_save_preferences"},
        "core_onboarding.py": {"handle_save_identity"},
        "core_watcher.py": {"handle_pattern_response"},
        "domain_sense_tool.py": {"handle_log_state"},
        "domain_learn_tool.py": {"handle_learn_add"},
        "domain_move_tool.py": {"handle_move_update", "_update_plan", "_update_workout",
                                "handle_push_to_watch", "handle_sync_garmin"},
        "domain_focus_tool.py": {"handle_brain_dump", "handle_create_task", "handle_update_task",
                                 "handle_set_reminder", "handle_cancel_reminder", "handle_add_goal",
                                 "handle_update_goal", "handle_delete_entry", "handle_focus_add",
                                 "handle_focus_update", "handle_save_to_effort"},
    }

    @staticmethod
    def _declared(node) -> bool:
        import ast
        if isinstance(node, ast.Call):
            return True
        if isinstance(node, ast.IfExp):
            return all(TestEveryWriteHandlerDeclaresItsOutcome._declared(n) for n in (node.body, node.orelse))
        return False

    def test_no_write_handler_returns_a_bare_string(self):
        import ast
        from pathlib import Path
        src = Path(__file__).parent.parent / "src" / "trellis"
        bare, seen = [], set()
        for filename, names in self.WRITE_HANDLERS.items():
            tree = ast.parse((src / filename).read_text())
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name in names:
                    seen.add(fn.name)
                    nested = {id(n) for inner in ast.walk(fn) if inner is not fn and isinstance(inner, (ast.FunctionDef, ast.Lambda))
                              for n in ast.walk(inner)}
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Return) and id(node) not in nested and node.value is not None \
                                and not self._declared(node.value):
                            bare.append(f"{filename}:{node.lineno} in {fn.name}")
        assert not bare, "undeclared outcomes:\n  " + "\n  ".join(bare)
        missing = set().union(*self.WRITE_HANDLERS.values()) - seen
        assert not missing, f"handlers not found (renamed?): {missing}"

    def test_every_tool_is_either_read_only_or_a_listed_write_handler(self):
        """A new tool must be placed on one side or the other."""
        from trellis.core_assembler import READ_ONLY_TOOLS
        assert READ_ONLY_TOOLS == {"focus_get", "sense_get", "learn_get", "move_get", "recall", "web_search"}
