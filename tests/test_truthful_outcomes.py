"""Stage 3: what the person is told follows what actually happened.

These are SOFTWARE guarantees, so they run against scripted models that behave
badly on purpose: claiming success before a tool fails, claiming it afterwards,
going silent, retrying blind. The checks read the recorded outcome and the
receipt the engine adds — never a word match on the model's own text."""
from __future__ import annotations

import pytest

from harness import Scenario, Step, run, tool
from trellis.core_actions import Status, failed, partial

SAVE_NOTE = tool("save_note", "Save a note.", text="The note.")
SAVE = (("save_note", {"text": "the boiler needs servicing"}),)


def _scenario(script, behaviour, name="x"):
    return run(Scenario(name=name, message="Save a note that the boiler needs servicing.",
                        tools={"save_note": (SAVE_NOTE, behaviour)}, script=script, checks=[]))


def _statuses(outcome):
    return [a.status for a in outcome.result.actions]


class TestAFailedActionIsNeverDeliveredAsSuccess:
    def test_success_claimed_before_the_tool_failed_then_silence(self):
        """The reviewed reproduction: 'Done — saved.', the save fails, the model says nothing more."""
        o = _scenario([Step(text="Done — saved.", tools=SAVE), Step(text=""), Step(text="")],
                      failed("Save failed; nothing changed."))
        assert _statuses(o) == [Status.FAILED]
        assert "Not done" in o.reply and "Save failed; nothing changed." in o.reply
        assert o.reply.index("Done — saved.") < o.reply.index("Not done")     # the correction has the last word

    def test_success_claimed_after_the_tool_failed(self):
        o = _scenario([Step(tools=SAVE), Step(text="Saved your note. There were no failures.")],
                      failed("Save failed; nothing changed."))
        assert _statuses(o) == [Status.FAILED]
        assert o.reply.rstrip().endswith("Save failed; nothing changed.")

    def test_a_model_silent_after_a_failure_is_asked_to_speak_even_if_it_spoke_earlier(self):
        o = _scenario([Step(text="On it.", tools=SAVE), Step(text=""), Step(text="That didn't save.")],
                      failed("Save failed; nothing changed."))
        assert len(o.model.said) == 1 and "That didn't save." in o.reply

    def test_a_clean_success_adds_nothing(self):
        o = _scenario([Step(tools=SAVE), Step(text="Saved your note.")], "Saved.")
        assert _statuses(o) == [Status.SUCCEEDED] and o.reply == "Saved your note."

    def test_partial_success_is_said_as_partial(self):
        o = _scenario([Step(tools=SAVE), Step(text="All done.")], partial("Saved the note; the vault page didn't update."))
        assert _statuses(o) == [Status.PARTIAL] and "Partly done" in o.reply


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
        tools = SimulatedTools({"save_note": (SAVE_NOTE, "Saved.")})
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
        tools = SimulatedTools({"save_note": (SAVE_NOTE, "Saved.")})
        assembler = self._assembler(ScriptedModel([Step(tools=SAVE)]), history, tools)   # then no more steps: it dies
        with pytest.raises(AssertionError):
            assembler.handle_turn(uuid4(), "Save a note that the boiler needs servicing.")
        role, line = history.rows[-1]
        assert role == "assistant" and "save_note → SUCCEEDED: Saved." in line


class TestARefusalTheModelCorrectsIsNotReportedAsAFailure:
    def test_a_failed_attempt_followed_by_the_same_tool_succeeding_leaves_no_warning(self):
        """A rule refused for being too long, rewritten shorter, saved: the person
        got what they asked for. Warning them about the first attempt is noise."""
        attempts = iter([failed("Not saved — 13 words, the limit is 10."), "Rule saved (global)."])
        o = _scenario([Step(tools=SAVE), Step(tools=(("save_note", {"text": "shorter"}),)), Step(text="Saved it.")],
                      lambda args: next(attempts))
        assert _statuses(o) == [Status.FAILED, Status.SUCCEEDED]      # both are on the record
        assert o.reply == "Saved it."                                 # and the person isn't warned

    def test_an_unknown_outcome_is_never_cleared_by_a_later_success(self):
        def behaviour(args):
            if args["text"] == "the boiler needs servicing":
                raise TimeoutError()
            return "Saved."
        o = _scenario([Step(tools=SAVE), Step(tools=(("save_note", {"text": "other"}),)), Step(text="Done.")], behaviour)
        assert "Not confirmed" in o.reply


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
