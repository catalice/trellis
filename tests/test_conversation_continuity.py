"""A reply to a proposal belongs to the proposal's house. "Yes" matches no room,
so it used to route to nothing — and the turn that CARRIES OUT what was agreed
ran without that house's guidance, context or preferences."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from trellis.core_assembler import Assembler
from trellis.core_oracle import OracleResult
from trellis.core_registry import TrellisRegistry


class _Oracle:
    def __init__(self): self.contexts = []
    def run(self, system, messages, tools, handlers, **kwargs):
        self.contexts.append(system.volatile)
        return OracleResult("ok")


class _History:
    def __init__(self, last_domains=None, minutes_ago=2):
        self.rows = []
        self._last = (sorted(last_domains), datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)) \
            if last_domains is not None else None
    def append(self, user_id, role, content, metadata=None): self.rows.append((role, content, metadata))
    def recent_window(self, user_id, *, since, cap): return []
    def to_messages(self, turns): return []
    def domain_summary(self, user_id, domain): return None
    def turn_count(self, user_id): return 0
    def max_turns_covered(self, user_id): return 0
    def last_routed(self, user_id): return self._last


class _Prefs:
    def get(self, user_id, domain):
        return {"move": "One exact prescription per session, never a range."}.get(domain)


def _assembler(history):
    registry = TrellisRegistry()
    registry.add_domain("move", lambda uid, now: "[Move] this week's plan", [], signals=["run", "workout", "watch"],
                        rooms=["intervals and long runs"])
    oracle = _Oracle()
    return Assembler(oracle=oracle, registry=registry, history=history, permanent=[], always_tools=[],
                     preferences=_Prefs()), oracle


def test_a_bare_yes_keeps_the_house_of_the_turn_it_answers():
    assembler, oracle = _assembler(_History(last_domains={"move"}))
    assembler.handle_turn(uuid4(), "yes")
    assert "One exact prescription per session" in oracle.contexts[0]
    assert "[Move] this week's plan" in oracle.contexts[0]


def test_a_message_that_names_its_own_house_is_not_overridden():
    history = _History(last_domains=set())
    assembler, oracle = _assembler(history)
    assembler.handle_turn(uuid4(), "push tomorrow's workout to my watch")
    assert "One exact prescription per session" in oracle.contexts[0]
    assert history.rows[0][2]["domains"] == ["move"]


def test_an_old_conversation_is_not_carried_into_a_new_one():
    assembler, oracle = _assembler(_History(last_domains={"move"}, minutes_ago=240))
    assembler.handle_turn(uuid4(), "yes")
    assert "One exact prescription per session" not in oracle.contexts[0]


def test_the_carried_house_is_recorded_so_a_second_short_reply_still_has_it():
    history = _History(last_domains={"move"})
    assembler, _ = _assembler(history)
    assembler.handle_turn(uuid4(), "ok go")
    assert history.rows[0][2]["domains"] == ["move"]
