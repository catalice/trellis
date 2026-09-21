"""Whose decision a change to the training week is.

In a real weekly review the plan was stored in the turn it was first suggested,
again after a life update, and again after an objection — never once agreed.
Two attempts to have Python tell from the person's WORDS whether they had
agreed, or had given an instruction, were each broken by an independent review:
"Is that plan okay?" approved a proposal; "I have pilates on Friday." rewrote
Friday. So nothing is read from words any more:

  - every change to the week is HELD as a proposal; the model has no way to store one
  - the person is sent the record itself, as its own message, with two buttons
  - only their press of "Store this" stores it — that record, that revision, once
  - the model's reply may not carry a second version of the plan beside it
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from harness import ScriptedModel, Step
from trellis.core_actions import PostgresActionLog, Status
from trellis.core_history import PostgresConversationHistory
from trellis.core_model import SystemPrompt
from trellis.core_oracle import Oracle
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_move_service import MoveService
from trellis.domain_move_tool import (
    MOVE_UPDATE_TOOL, PlanDecisions, handle_move_update, move_context_loader, move_snapshot, render_proposal,
)

TZ = ZoneInfo("UTC")
T1 = datetime(2026, 3, 8, 21, 0, tzinfo=timezone.utc)      # a Sunday evening
T2 = T1 + timedelta(minutes=3)
T3 = T2 + timedelta(minutes=3)

WEEK = {"arc": "holding volume", "week": [
    {"date": "2026-03-09", "type": "easy", "detail": "30min"},
    {"date": "2026-03-11", "type": "intervals", "detail": "4x4min"},
    {"date": "2026-03-13", "type": "easy", "detail": "35min"},
    {"date": "2026-03-15", "type": "long", "detail": "90min"},
]}
WEEK_60 = {**WEEK, "week": [*WEEK["week"][:3], {"date": "2026-03-15", "type": "long", "detail": "60min"}]}


class _Goals:
    def list_training_goals(self, user_id): return []


@pytest.fixture
def app(pg_database, pg_user):
    move = MoveService(PostgresMoveRepository(pg_database, TZ), _Goals(), TZ)
    history = PostgresConversationHistory(pg_database, TZ)
    decisions = PlanDecisions(move, action_log=lambda uid: PostgresActionLog(pg_database, uid), history=history)
    return move, decisions, pg_user, pg_database


def _stored(move, user):
    plan = move.get_plan(user)
    return (plan.plan.get("week") if plan else None) or []


def _propose(move, user, plan=WEEK, at=T1, **more):
    result = handle_move_update(user, {"what": "plan", "plan": plan, **more}, at, move_service=move)
    assert result.status is Status.SUCCEEDED, result
    return move.open_proposal(user)


def _press(decisions, user, choice, proposal, at=T2):
    return decisions.decide(user, f"plan:{choice}:{proposal.id}", at)


class TestTheModelCannotStoreAWeek:
    @pytest.mark.parametrize("extra", [
        {}, {"instructed": "I have pilates on Friday."}, {"instructed": "Put 30 minutes easy on Friday."},
        {"agree": "yes"}, {"replace_week": True},
    ])
    def test_whatever_it_sends_the_week_is_only_held(self, app, extra):
        """Every reviewed way of storing from the model's side, now inert."""
        move, _, user, _ = app
        result = handle_move_update(user, {"what": "plan", "plan": WEEK, **extra}, T1, move_service=move)
        assert result.startswith("PROPOSED — NOT STORED")
        assert _stored(move, user) == []

    def test_a_pending_proposal_cannot_be_agreed_through_the_tool(self, app):
        """'Is that plan okay?' + the model calling agree used to store it."""
        move, _, user, _ = app
        held = _propose(move, user)
        result = handle_move_update(user, {"what": "plan", "agree": str(held.id)}, T2, move_service=move)
        assert result.status is Status.FAILED
        assert _stored(move, user) == []

    @pytest.mark.parametrize("entries, why", [
        ([{"date": "2026-03-09", "type": "strength", "detail": "gym"}, {"date": "2026-03-09", "type": "easy", "detail": "30min"}],
         "two entries for 2026-03-09"),
        ([{"date": "next monday", "type": "easy", "detail": "30min"}], "real date"),
        ([], "no dated days"),
    ])
    def test_what_cannot_be_stored_as_shown_is_not_held(self, app, entries, why):
        move, _, user, _ = app
        result = handle_move_update(user, {"what": "plan", "plan": {"week": entries}}, T1, move_service=move)
        assert result.status is Status.FAILED and result.correctable and why in result
        assert move.open_proposal(user) is None


class TestOnlyTheirPressStoresIt:
    def test_the_proposal_waits_to_be_sent_once_as_the_record_with_two_buttons(self, app):
        move, decisions, user, _ = app
        held = _propose(move, user)
        [waiting] = decisions.waiting(user)
        assert waiting.text == render_proposal(held) and "Sun 15 Mar — long: 90min" in waiting.text
        assert [label for label, _ in waiting.buttons] == ["Store this", "Change it"]
        assert all(str(held.id) in data and len(data.encode()) <= 64 for _, data in waiting.buttons)   # Telegram's limit
        decisions.delivered(user, waiting, T1)
        assert decisions.waiting(user) == []                   # sent once; not again after every turn

    def test_a_send_that_failed_is_still_waiting(self, app):
        move, decisions, user, _ = app
        _propose(move, user)
        assert len(decisions.waiting(user)) == 1 and len(decisions.waiting(user)) == 1    # until marked delivered

    def test_store_this_stores_that_record_whole(self, app):
        move, decisions, user, _ = app
        held = _propose(move, user)
        outcome = _press(decisions, user, "store", held)
        assert outcome.startswith("Stored, exactly as shown:") and "Sun 15 Mar — long: 90min" in outcome
        assert _stored(move, user) == WEEK["week"]
        assert move.open_proposal(user) is None

    def test_a_second_press_changes_nothing(self, app):
        move, decisions, user, _ = app
        held = _propose(move, user)
        _press(decisions, user, "store", held)
        again = _press(decisions, user, "store", held, at=T3)
        assert "wasn't stored again" in again
        assert _stored(move, user) == WEEK["week"]

    def test_the_button_under_a_replaced_proposal_stores_nothing(self, app):
        """Approval is bound to the exact revision: the old message's button is dead."""
        move, decisions, user, _ = app
        first = _propose(move, user)
        second = _propose(move, user, WEEK_60, at=T2)
        assert "superseded" in _press(decisions, user, "store", first, at=T3)
        assert _stored(move, user) == []
        _press(decisions, user, "store", second, at=T3)
        assert _stored(move, user)[-1]["detail"] == "60min"

    def test_change_it_stores_nothing_and_retires_that_proposal(self, app):
        move, decisions, user, _ = app
        held = _propose(move, user)
        assert "Nothing stored" in _press(decisions, user, "change", held)
        assert _stored(move, user) == [] and move.open_proposal(user) is None
        assert "withdrawn" in _press(decisions, user, "store", held, at=T3)      # its Store button is dead too
        assert _stored(move, user) == []

    def test_a_proposal_whose_days_have_all_passed_cannot_be_stored(self, app):
        move, decisions, user, _ = app
        held = _propose(move, user)
        later = _press(decisions, user, "store", held, at=T1 + timedelta(days=30))
        assert "out of date" in later and _stored(move, user) == []

    def test_someone_elses_proposal_and_nonsense_are_refused(self, app, pg_database):
        move, decisions, user, _ = app
        held = _propose(move, user)
        stranger = pg_database.ensure_user(int(uuid4().int % 10**9), "UTC")
        assert "can't find" in decisions.decide(stranger, f"plan:store:{held.id}", T2)
        for data in ("plan:store:not-an-id", "plan:delete:" + str(held.id), "garbage", ""):
            assert "Nothing was changed" in decisions.decide(user, data, T2)
        assert _stored(move, user) == []

    def test_a_press_is_on_the_record_like_any_action(self, app):
        move, decisions, user, database = app
        held = _propose(move, user)
        _press(decisions, user, "store", held)
        with database.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT tool, status, input->>'choice', input->>'proposal' FROM action_log WHERE user_id = %s", (user,))
            assert cur.fetchall() == [("plan_decision", "succeeded", "store", str(held.id))]

    def test_a_press_that_cannot_be_recorded_does_not_run(self, app):
        move, _, user, _ = app
        class _NoLog:
            def begin(self, tool, input): return None
        held = _propose(move, user)
        outcome = PlanDecisions(move, action_log=lambda uid: _NoLog()).decide(user, f"plan:store:{held.id}", T2)
        assert "haven't done it" in outcome and _stored(move, user) == []

    def test_what_they_were_sent_and_what_they_pressed_are_in_the_conversation(self, app):
        move, decisions, user, database = app
        held = _propose(move, user)
        [waiting] = decisions.waiting(user)
        decisions.delivered(user, waiting, T1)
        _press(decisions, user, "store", held)
        said = [t.content for t in PostgresConversationHistory(database, TZ).recent(user, limit=5)]
        assert any("Sent to them as its own message" in c and "long: 90min" in c for c in said)
        assert any("They pressed Store this" in c and "Stored, exactly as shown" in c for c in said)


class TestTheProposalIsTheOnlyPlanTheyAreShown:
    """Held 90 minutes; the reply said '30 minutes. Shall I store that?'. Correct
    buttons under a contradictory message still leave them checking the work."""

    def _reply(self, user, move, *said: str) -> str:
        model = ScriptedModel([Step(tools=(("move_update", {"what": "plan", "plan": WEEK}),)), *(Step(text=t) for t in said)])
        return Oracle(model).run(
            SystemPrompt(stable="", volatile=""), [{"role": "user", "content": "Plan my week?"}],
            [MOVE_UPDATE_TOOL], {"move_update": lambda inp: handle_move_update(user, inp, T1, move_service=move)}).text

    def test_a_competing_quantity_does_not_reach_them(self, app):
        move, _, user, _ = app
        reply = self._reply(user, move, "You look tired, so I'd keep Sunday short — 30 minutes. It's your call.")
        assert "30" not in reply and "It's your call." in reply

    def test_a_schedule_in_the_models_own_words_does_not_reach_them(self, app):
        move, _, user, _ = app
        reply = self._reply(user, move, "Both tasks are marked done.\n\n- Mon: easy\n- **Fri** — rest day\n- Sun 15: long run\n\n"
                                        "I held volume because last night's sleep was poor.")
        assert "Both tasks are marked done." in reply and "I held volume because last night's sleep was poor." in reply
        assert "Mon" not in reply and "Fri" not in reply and "Sun 15" not in reply

    def test_the_rest_of_what_they_are_owed_survives(self, app):
        move, _, user, _ = app
        reply = self._reply(user, move, "The venue is still open and due Saturday. Two tasks are overdue. I kept every run that fits.")
        assert reply == "The venue is still open and due Saturday. Two tasks are overdue. I kept every run that fits."

    def test_a_reply_that_was_nothing_but_a_second_plan_still_says_something(self, app):
        move, _, user, _ = app
        assert self._reply(user, move, "Mon 30min, Wed 4x4min, Sun 90min.") == "I've put a week together — it's in the next message."

    def test_a_turn_that_proposed_nothing_is_left_alone(self, app):
        _, _, _, _ = app
        model = ScriptedModel([Step(text="Your long run on Sunday was 80 minutes at 149 bpm.")])
        text = Oracle(model).run(SystemPrompt(stable="", volatile=""), [{"role": "user", "content": "How was Sunday?"}], [], {}).text
        assert text == "Your long run on Sunday was 80 minutes at 149 bpm."


class TestUnfinishedBusinessIsARecord:
    def test_an_unanswered_proposal_is_in_front_of_the_next_turn(self, app):
        move, _, user, _ = app
        _propose(move, user)
        tomorrow = T1 + timedelta(days=1)
        context = move_context_loader(move, _Goals())(user, tomorrow)
        assert "PROPOSED BY YOU, NOT STORED" in context and "2026-03-13 — easy: 35min" in context
        assert "waiting for their answer" in move_snapshot(move)(user, tomorrow)

    def test_once_answered_it_is_gone_from_both(self, app):
        move, decisions, user, _ = app
        _press(decisions, user, "store", _propose(move, user))
        assert "NOT STORED" not in move_context_loader(move, _Goals())(user, T2)
        assert "waiting" not in (move_snapshot(move)(user, T2) or "")
