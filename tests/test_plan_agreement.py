"""Whose decision a plan change is.

In a real weekly review the plan was stored in the turn it was first suggested,
stored again after a life update, and a third time after an objection — never
once agreed. "Get their yes" was a rule in a prompt. Now it is a record:

  their instruction  — their own words, found in the message being answered: stored now
  Trellis's proposal — held; the stored plan is untouched
  their yes          — a LATER turn; what is stored is the proposal they were shown
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trellis.core_actions import Status
from trellis.core_history import SCHEDULED_TURN, PostgresConversationHistory
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_move_service import MoveService
from trellis.domain_move_tool import handle_move_update, move_context_loader, move_snapshot

TZ = ZoneInfo("UTC")
T1 = datetime(2026, 3, 8, 21, 0, tzinfo=timezone.utc)      # the turn that proposes
T2 = T1 + timedelta(minutes=3)                             # the turn that answers

WEEK = {"arc": "holding volume", "week": [
    {"date": "2026-03-09", "type": "easy", "detail": "30min"},
    {"date": "2026-03-11", "type": "intervals", "detail": "4x4min"},
    {"date": "2026-03-13", "type": "easy", "detail": "35min"},
    {"date": "2026-03-15", "type": "long", "detail": "65min"},
]}


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


def _proposal_id(result: str) -> str:
    return result.split("Held as proposal ", 1)[1].split(".", 1)[0]


class TestAProposalIsNotAChange:
    def test_a_suggested_week_is_held_and_the_plan_is_untouched(self, move):
        service, user, _ = move
        result = handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service)
        assert result.status is Status.SUCCEEDED and result.startswith("PROPOSED — NOT STORED")
        assert "2026-03-13 — easy: 35min" in result                  # told exactly what to show them
        assert _stored(service, user) == []

    def test_it_cannot_be_agreed_in_the_turn_that_made_it(self, move):
        """They haven't seen it. This is the fault itself: proposed and saved in one breath."""
        service, user, _ = move
        held = _proposal_id(handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service))
        result = handle_move_update(user, {"what": "plan", "agree": held}, T1, move_service=service)
        assert result.status is Status.FAILED and "THIS turn" in result
        assert _stored(service, user) == []

    def test_their_yes_stores_the_proposal_they_were_shown_not_a_new_one(self, move):
        service, user, _ = move
        held = _proposal_id(handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service))
        different = {"arc": "something else", "week": [{"date": "2026-03-13", "type": "intervals", "detail": "6x800m"}]}
        result = handle_move_update(user, {"what": "plan", "agree": held, "plan": different}, T2, move_service=service)
        assert result.status is Status.SUCCEEDED
        assert _stored(service, user) == WEEK["week"]                # all four days, Friday still easy
        assert service.open_proposal(user) is None

    def test_a_second_proposal_replaces_the_first_and_only_it_can_be_agreed(self, move):
        service, user, _ = move
        first = _proposal_id(handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service))
        revised = {**WEEK, "week": [*WEEK["week"][:3], {"date": "2026-03-15", "type": "long", "detail": "80min"}]}
        second = _proposal_id(handle_move_update(user, {"what": "plan", "plan": revised}, T2, move_service=service))
        late = T2 + timedelta(minutes=2)
        stale = handle_move_update(user, {"what": "plan", "agree": first}, late, move_service=service)
        assert stale.status is Status.FAILED and "superseded" in stale
        assert handle_move_update(user, {"what": "plan", "agree": second}, late, move_service=service).status is Status.SUCCEEDED
        assert _stored(service, user)[-1]["detail"] == "80min"

    def test_a_no_withdraws_it(self, move):
        service, user, _ = move
        handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service)
        assert handle_move_update(user, {"what": "plan", "agree": "withdraw"}, T2, move_service=service).status is Status.SUCCEEDED
        assert service.open_proposal(user) is None and _stored(service, user) == []


class TestTheirInstructionIsTheAuthorisation:
    def test_what_they_asked_for_is_stored_without_asking_again(self, move):
        service, user, said = move
        said["message"] = "Put an easy run on Thursday — 30 minutes is fine."
        day = {"week": [{"date": "2026-03-12", "type": "easy", "detail": "30min"}]}
        result = handle_move_update(user, {"what": "plan", "plan": day, "instructed": "put an easy run on Thursday"},
                                    T1, move_service=service)
        assert result.status is Status.SUCCEEDED and "Merged 1 day(s) in" in result
        assert _stored(service, user) == day["week"]

    def test_words_they_never_said_are_not_an_instruction(self, move):
        service, user, said = move
        said["message"] = "I'd rather not run myself into the ground this week."
        result = handle_move_update(user, {"what": "plan", "plan": WEEK, "instructed": "cut the long run down please"},
                                    T1, move_service=service)
        assert result.status is Status.FAILED and result.correctable
        assert _stored(service, user) == []

    def test_a_yes_is_not_an_instruction(self, move):
        """Found by the real model on the first evaluation: it described a week
        in prose, got a yes, and stored it by quoting the yes as their instruction."""
        service, user, said = move
        said["message"] = "Yes, go with that."
        result = handle_move_update(user, {"what": "plan", "plan": WEEK, "instructed": "Yes, go with that."},
                                    T1, move_service=service)
        assert result.status is Status.FAILED and result.correctable and "SHOWN" in result
        assert _stored(service, user) == []

    def test_a_couple_of_common_words_are_not_enough(self, move):
        service, user, said = move
        said["message"] = "Yes, go with that."
        assert not service.asked_for(user, "go with")


class TestUnfinishedBusinessIsARecord:
    def test_an_unanswered_proposal_is_in_front_of_the_next_turn(self, move):
        service, user, _ = move
        held = _proposal_id(handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service))
        tomorrow = T1 + timedelta(days=1)
        context = move_context_loader(service, _Goals())(user, tomorrow)
        assert f"NOT AGREED, NOT STORED — proposal {held}" in context and "2026-03-13 — easy: 35min" in context
        assert "waiting for their answer" in move_snapshot(service)(user, tomorrow)

    def test_once_answered_it_is_gone_from_both(self, move):
        service, user, _ = move
        held = _proposal_id(handle_move_update(user, {"what": "plan", "plan": WEEK}, T1, move_service=service))
        handle_move_update(user, {"what": "plan", "agree": held}, T2, move_service=service)
        assert "NOT AGREED" not in move_context_loader(service, _Goals())(user, T2)
        assert "waiting" not in (move_snapshot(service)(user, T2) or "")


class TestAScheduledCheckInIsNotThemSpeaking:
    def test_its_wording_can_never_count_as_their_instruction(self, pg_database, pg_user):
        history = PostgresConversationHistory(pg_database, TZ)
        history.append(pg_user, "user", "Move Thursday's run to Friday please.")
        assert history.their_last_message(pg_user) == "Move Thursday's run to Friday please."
        history.append(pg_user, "user", f'{SCHEDULED_TURN} This is the instruction they set: "plan next week".]')
        assert history.their_last_message(pg_user) is None
