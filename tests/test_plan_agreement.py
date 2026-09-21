"""Whose decision a plan change is.

In a real weekly review the plan was stored in the turn it was first suggested,
again after a life update, and again after an objection — never once agreed.
"Get their yes" was a rule in a prompt. Now:

  their instruction  — their own words, from a sentence of the message being
                       answered that is neither a question nor a negation; ONLY
                       the days those words name may change
  Trellis's proposal — held as a record; the stored plan is untouched; the
                       PERSON is shown the record itself, not a retelling
  their yes          — decided by Python from their message (the whole message,
                       assent and nothing else), in a LATER turn; what is
                       stored is the record they were shown

What this does NOT do: read meaning. It bounds what can happen without it — and
where it can't tell, it holds a proposal and costs one more "yes". (The first
version trusted the model to say whether they had agreed; an independent review
reproduced a stored plan after "I do not want to change my week.")
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from harness import ScriptedModel, Step
from trellis.core_actions import Status
from trellis.core_history import SCHEDULED_TURN, PostgresConversationHistory
from trellis.core_model import SystemPrompt
from trellis.core_oracle import Oracle
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_move_service import MoveService
from trellis.domain_move_tool import MOVE_UPDATE_TOOL, handle_move_update, move_context_loader, move_snapshot

TZ = ZoneInfo("UTC")
T1 = datetime(2026, 3, 8, 21, 0, tzinfo=timezone.utc)      # Sunday: the turn that proposes
T2 = T1 + timedelta(minutes=3)                             # the turn that answers
T3 = T2 + timedelta(minutes=3)

WEEK = {"arc": "holding volume", "week": [
    {"date": "2026-03-09", "type": "easy", "detail": "30min"},          # Monday
    {"date": "2026-03-11", "type": "intervals", "detail": "4x4min"},    # Wednesday
    {"date": "2026-03-13", "type": "easy", "detail": "35min"},          # Friday
    {"date": "2026-03-15", "type": "long", "detail": "90min"},          # Sunday
]}
THURSDAY = {"week": [{"date": "2026-03-12", "type": "easy", "detail": "30min"}]}
SUNDAY_60 = {"week": [{"date": "2026-03-15", "type": "long", "detail": "60min"}]}


class _Goals:
    def list_training_goals(self, user_id): return []


@pytest.fixture
def move(pg_database, pg_user):
    said = {"message": "Can we do our weekly review?"}
    service = MoveService(PostgresMoveRepository(pg_database, TZ), _Goals(), TZ,
                          their_message=lambda uid: said["message"])
    return service, pg_user, said


def _stored(service, user):
    plan = service.get_plan(user)
    return (plan.plan.get("week") if plan else None) or []


def _propose(service, user, plan=WEEK, at=T1):
    result = handle_move_update(user, {"what": "plan", "plan": plan}, at, move_service=service)
    assert result.status is Status.SUCCEEDED, result
    return result, result.split("Held as proposal ", 1)[1].split(".", 1)[0]


def _agree(service, user, held, at=T2):
    return handle_move_update(user, {"what": "plan", "agree": held}, at, move_service=service)


class TestAProposalIsNotAChange:
    def test_a_suggested_week_is_held_and_the_plan_is_untouched(self, move):
        service, user, _ = move
        result, _ = _propose(service, user)
        assert result.startswith("PROPOSED — NOT STORED")
        assert _stored(service, user) == []

    def test_it_cannot_be_agreed_in_the_turn_that_made_it(self, move):
        service, user, said = move
        _, held = _propose(service, user)
        said["message"] = "Yes, go with that."
        result = _agree(service, user, held, at=T1)
        assert result.status is Status.FAILED and "THIS turn" in result
        assert _stored(service, user) == []

    def test_a_second_proposal_replaces_the_first_and_only_it_can_be_agreed(self, move):
        service, user, said = move
        _, first = _propose(service, user)
        _, second = _propose(service, user, {**WEEK, "week": [*WEEK["week"][:3], SUNDAY_60["week"][0]]}, at=T2)
        said["message"] = "Yes please."
        stale = _agree(service, user, first, at=T3)
        assert stale.status is Status.FAILED and "superseded" in stale
        assert _agree(service, user, second, at=T3).status is Status.SUCCEEDED
        assert _stored(service, user)[-1]["detail"] == "60min"

    @pytest.mark.parametrize("entries, why", [
        ([{"date": "2026-03-09", "type": "strength", "detail": "gym"}, {"date": "2026-03-09", "type": "easy", "detail": "30min"}],
         "two entries for 2026-03-09"),                       # shown as two, stored as one
        ([{"date": "next monday", "type": "easy", "detail": "30min"}], "real date"),   # used to be dropped silently
        ([], "no dated days"),
    ])
    def test_what_cannot_be_stored_as_shown_is_not_held(self, move, entries, why):
        service, user, _ = move
        result = handle_move_update(user, {"what": "plan", "plan": {"week": entries}}, T1, move_service=service)
        assert result.status is Status.FAILED and result.correctable and why in result
        assert service.open_proposal(user) is None


class TestTheirYesIsDecidedFromTheirMessage:
    """The agree path checked a proposal's status and age — never whether the
    message being answered agreed. The model said they had; that was enough."""

    @pytest.mark.parametrize("message", [
        "Yes, go with that.", "yes please", "OK great, store it", "Sounds good, thanks!", "Yes, use that plan.", "Perfect",
    ])
    def test_plain_assent_stores_the_held_proposal_whole(self, move, message):
        service, user, said = move
        _, held = _propose(service, user)
        said["message"] = message
        other = {"week": [{"date": "2026-03-13", "type": "intervals", "detail": "6x800m"}]}
        result = handle_move_update(user, {"what": "plan", "agree": held, "plan": other}, T2, move_service=service)
        assert result.status is Status.SUCCEEDED
        assert _stored(service, user) == WEEK["week"]          # the record — not what came with the yes
        assert service.open_proposal(user) is None

    @pytest.mark.parametrize("message", [
        "I do not want to change my week.",                    # a refusal
        "No.",
        "Why is the long run 90 minutes?",                     # a question
        "Yes, but make Friday shorter.",                       # a yes with a change in it
        "Before I agree — does last night change anything?",
        "Hmm, not sure.",
        "",                                                    # nobody spoke: a scheduled turn
    ])
    def test_anything_else_stores_nothing(self, move, message):
        service, user, said = move
        _, held = _propose(service, user)
        said["message"] = message
        result = _agree(service, user, held)
        assert result.status is Status.FAILED and "not a plain yes" in result
        assert _stored(service, user) == []
        assert service.open_proposal(user) is not None         # still theirs to answer

    def test_with_no_way_to_read_their_message_nothing_is_agreed(self, pg_database, pg_user):
        service = MoveService(PostgresMoveRepository(pg_database, TZ), _Goals(), TZ)     # no their_message
        _, held = _propose(service, pg_user)
        assert _agree(service, pg_user, held).status is Status.FAILED


class TestTheirInstructionChangesOnlyWhatItNames:
    def test_what_they_asked_for_is_stored_without_asking_again(self, move):
        service, user, said = move
        said["message"] = "Put an easy run on Thursday — 30 minutes is fine."
        result = handle_move_update(user, {"what": "plan", "plan": THURSDAY, "instructed": "put an easy run on Thursday"},
                                    T1, move_service=service)
        assert result.status is Status.SUCCEEDED and "Merged 1 day(s) in" in result
        assert _stored(service, user) == THURSDAY["week"]

    @pytest.mark.parametrize("message, quoted, plan, why", [
        ("I do not want to change my week.", "I do not want to change my week", WEEK, "NOT to do"),
        ("I don't want to run on Thursday.", "run on Thursday", THURSDAY, "NOT to do"),   # quoting around the negation
        ("Yes, use that plan.", "Yes, use that plan", WEEK, "don't name"),
        ("Should I put an easy run on Thursday?", "put an easy run on Thursday", THURSDAY, "question"),
        ("Put an easy run on Thursday.", "put an easy run on Thursday", SUNDAY_60, "don't name Sunday"),
        ("Put an easy run on Thursday.", "put an easy run on Thursday",
         {"week": [*THURSDAY["week"], *SUNDAY_60["week"]]}, "don't name Sunday"),          # Thursday AND a rider
        ("I'd rather take it easy.", "cut the long run down", WEEK, "not in the message"),
    ])
    def test_what_is_not_their_instruction_is_not_carried_out(self, move, message, quoted, plan, why):
        service, user, said = move
        said["message"] = message
        result = handle_move_update(user, {"what": "plan", "plan": plan, "instructed": quoted}, T1, move_service=service)
        assert result.status is Status.FAILED and result.correctable and why in result, result
        assert _stored(service, user) == []

    def test_an_instruction_cannot_replace_the_whole_week(self, move):
        service, user, said = move
        said["message"] = "Put an easy run on Thursday."
        result = handle_move_update(user, {"what": "plan", "plan": THURSDAY, "replace_week": True,
                                           "instructed": "put an easy run on Thursday"}, T1, move_service=service)
        assert result.status is Status.FAILED and "drops days" in result

    def test_it_retires_a_waiting_proposal_it_has_overtaken(self, move):
        """Saving Sunday as 60 minutes left a proposal for 90 waiting to be agreed."""
        service, user, said = move
        _, held = _propose(service, user)
        said["message"] = "Make the long run on Sunday 60 minutes."
        result = handle_move_update(user, {"what": "plan", "plan": SUNDAY_60, "instructed": "make the long run on Sunday 60 minutes"},
                                    T2, move_service=service)
        assert result.status is Status.SUCCEEDED and "retired" in result
        assert service.open_proposal(user) is None
        said["message"] = "Yes."
        assert _agree(service, user, held, at=T3).status is Status.FAILED
        assert _stored(service, user) == SUNDAY_60["week"]

    def test_one_about_other_days_leaves_the_proposal_waiting(self, move):
        service, user, said = move
        _propose(service, user)
        said["message"] = "Put an easy run on Thursday."
        handle_move_update(user, {"what": "plan", "plan": THURSDAY, "instructed": "put an easy run on Thursday"},
                           T2, move_service=service)
        assert service.open_proposal(user) is not None


class TestWhatTheySeeIsTheRecord:
    """The held proposal went to the MODEL, which wrote what the person saw:
    held 90 minutes, told them 30, stored 90 on their yes, replied 'as agreed'."""

    def test_the_proposal_reaches_them_word_for_word_whatever_the_model_says(self, move):
        service, user, _ = move
        model = ScriptedModel([
            Step(tools=(("move_update", {"what": "plan", "plan": WEEK}),)),
            Step(text="I'd keep Sunday's long run short — 30 minutes. Shall I store that?"),     # not what is held
        ])
        result = Oracle(model).run(
            SystemPrompt(stable="", volatile=""), [{"role": "user", "content": "Plan my week?"}],
            [MOVE_UPDATE_TOOL], {"move_update": lambda inp: handle_move_update(user, inp, T1, move_service=service)})
        assert "Sun 15 Mar — long: 90min" in result.text                   # the record, from Python
        assert result.text.index("30 minutes") < result.text.index("PROPOSED — not stored yet:")
        assert result.text.rstrip().endswith("Reply yes to store exactly this, or tell me what to change.")
        held = service.open_proposal(user)
        for session in held.plan["week"]:                                  # every held day is in what they saw
            assert session["detail"] in result.text

    def test_a_proposal_revised_within_a_turn_is_shown_once_and_current(self, move):
        service, user, _ = move
        model = ScriptedModel([
            Step(tools=(("move_update", {"what": "plan", "plan": WEEK}),)),
            Step(tools=(("move_update", {"what": "plan", "plan": {"week": [*WEEK["week"][:3], SUNDAY_60["week"][0]]}}),)),
            Step(text="Here's what I'd suggest."),
        ])
        text = Oracle(model).run(
            SystemPrompt(stable="", volatile=""), [{"role": "user", "content": "Plan my week?"}],
            [MOVE_UPDATE_TOOL], {"move_update": lambda inp: handle_move_update(user, inp, T1, move_service=service)}).text
        assert text.count("PROPOSED — not stored yet:") == 1 and "60min" in text and "90min" not in text


class TestUnfinishedBusinessIsARecord:
    def test_an_unanswered_proposal_is_in_front_of_the_next_turn(self, move):
        service, user, _ = move
        _, held = _propose(service, user)
        tomorrow = T1 + timedelta(days=1)
        context = move_context_loader(service, _Goals())(user, tomorrow)
        assert f"NOT AGREED, NOT STORED — proposal {held}" in context and "2026-03-13 — easy: 35min" in context
        assert "waiting for their answer" in move_snapshot(service)(user, tomorrow)

    def test_once_answered_it_is_gone_from_both(self, move):
        service, user, said = move
        _, held = _propose(service, user)
        said["message"] = "Yes."
        assert _agree(service, user, held).status is Status.SUCCEEDED
        assert "NOT AGREED" not in move_context_loader(service, _Goals())(user, T2)
        assert "waiting" not in (move_snapshot(service)(user, T2) or "")


class TestAScheduledCheckInIsNotThemSpeaking:
    def test_its_wording_is_never_their_message(self, pg_database, pg_user):
        history = PostgresConversationHistory(pg_database, TZ)
        history.append(pg_user, "user", "Move Thursday's run to Friday please.")
        assert history.their_last_message(pg_user) == "Move Thursday's run to Friday please."
        history.append(pg_user, "user", f'{SCHEDULED_TURN} This is the instruction they set: "yes please plan next week".]')
        assert history.their_last_message(pg_user) is None
