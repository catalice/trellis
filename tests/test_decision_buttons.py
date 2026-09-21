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
from trellis.domain_move_tool import Decision

UID = uuid4()
ASKED = Decision(ref=uuid4(), text="Proposed — not stored yet:\nSun 15 Mar — long: 90min",
                 buttons=(("Store this", "plan:store:abc"), ("Change it", "plan:change:abc")))


class _Decisions:
    def __init__(self, waiting=()):
        self._waiting, self.delivered_refs, self.presses = list(waiting), [], []
    def waiting(self, user_id): return list(self._waiting)
    def delivered(self, user_id, decision, now): self.delivered_refs.append(decision.ref)
    def decide(self, user_id, data, now):
        self.presses.append((user_id, data))
        return "Stored, exactly as shown:\nSun 15 Mar — long: 90min"


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
