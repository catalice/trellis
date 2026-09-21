"""The Telegram side of a decision: what is waiting goes out as its own message
with its buttons; a press comes back through the same door as a message — a
private chat with an allowed person, or nothing — and is decided without a model."""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from types import SimpleNamespace
from uuid import uuid4

from trellis.core_telegram import TelegramTrellis
from trellis.domain_move_tool import Decision, Pressed

UID = uuid4()
ASKED = Decision(ref=uuid4(), text="Proposed — not stored yet:\nSun 15 Mar — long: 90min",
                 buttons=(("Store this", "plan:store:abc"), ("Change it", "plan:change:abc")))


class _Decisions:
    def __init__(self, waiting=(), outcome=Pressed("Stored, exactly as shown:\nSun 15 Mar — long: 90min")):
        self._waiting, self.delivered_refs, self.presses, self.outcome = list(waiting), [], [], outcome
    def waiting(self, user_id): return list(self._waiting)
    def delivered(self, user_id, decision, now): self.delivered_refs.append(decision.ref)
    def decide(self, user_id, data, now):
        self.presses.append((user_id, data))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _Bot:
    def __init__(self, fail=False): self.sent, self.fail = [], fail
    async def send_message(self, **kw):
        if self.fail:
            raise TimeoutError("telegram did not answer")
        self.sent.append(kw)
        return SimpleNamespace(message_id=len(self.sent), chat_id=kw["chat_id"])


def _app(decisions, allowed=frozenset({12345})):
    app = TelegramTrellis.__new__(TelegramTrellis)
    app.settings = SimpleNamespace(telegram_allowed_users=allowed, timezone=timezone.utc)
    app.database = SimpleNamespace(ensure_user=lambda telegram_id, tz: UID)
    app._decisions, app._message_log, app._turn_locks = decisions, None, {}
    app.logger = logging.getLogger("test")
    return app


def test_a_waiting_proposal_goes_out_as_its_own_message_with_its_buttons():
    decisions, bot = _Decisions([ASKED]), _Bot()
    asyncio.run(_app(decisions)._send_waiting_decisions(bot, 12345, UID))
    [message] = bot.sent
    assert message["text"] == ASKED.text and "parse_mode" not in message          # the record, untouched by formatting
    buttons = message["reply_markup"].inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in buttons] == list(ASKED.buttons)
    assert decisions.delivered_refs == [ASKED.ref]


def test_a_send_telegram_did_not_take_leaves_it_waiting():
    decisions = _Decisions([ASKED])
    asyncio.run(_app(decisions)._send_waiting_decisions(_Bot(fail=True), 12345, UID))
    assert decisions.delivered_refs == []


def _press(app, *, chat_type="private", telegram_id=12345, data="plan:store:abc"):
    state = {"answered": 0, "buttons_removed": 0}
    async def answer(): state["answered"] += 1
    async def edit_message_reply_markup(reply_markup=None): state["buttons_removed"] += 1
    bot = _Bot()
    update = SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=answer, edit_message_reply_markup=edit_message_reply_markup),
        effective_chat=SimpleNamespace(type=chat_type, id=telegram_id), effective_user=SimpleNamespace(id=telegram_id))
    asyncio.run(app.pressed(update, SimpleNamespace(application=SimpleNamespace(bot=bot))))
    return state, bot


def test_a_press_is_decided_without_a_model_and_the_outcome_is_sent():
    decisions = _Decisions()
    state, bot = _press(_app(decisions))
    assert decisions.presses == [(UID, "plan:store:abc")]
    assert state == {"answered": 1, "buttons_removed": 1}
    assert bot.sent[0]["text"].startswith("Stored, exactly as shown:")


def test_a_press_from_a_group_or_a_stranger_does_nothing():
    for kwargs in ({"chat_type": "group"}, {"telegram_id": 999}):
        decisions = _Decisions()
        _, bot = _press(_app(decisions), **kwargs)
        assert decisions.presses == [] and bot.sent == []


def test_when_nothing_ran_the_buttons_stay_so_there_is_something_to_press_again():
    """The refusal said 'press again in a moment' and then took the buttons away;
    the proposal was already marked delivered, so nothing ever brought them back."""
    decisions = _Decisions(outcome=Pressed("I couldn't put that on record… press again in a moment.", buttons_stay=True))
    state, bot = _press(_app(decisions))
    assert state["buttons_removed"] == 0
    assert "press again" in bot.sent[0]["text"]


def test_a_press_that_blew_up_is_not_offered_again_blind():
    state, bot = _press(_app(_Decisions(outcome=RuntimeError("boom"))))
    assert state["buttons_removed"] == 1 and "Ask me what's stored" in bot.sent[0]["text"]
