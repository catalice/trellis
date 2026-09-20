"""The summariser hands the conversation over as ONE document — as chat turns,
a model continues the chat instead of recording it."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from trellis.core_summariser import make_summariser


class FakeGroq:
    def __init__(self, reply):
        self.reply, self.sent = reply, None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.sent = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


class FakeAnthropic:
    def __init__(self, reply):
        self.reply, self.sent = reply, None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.sent = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


class FakeHistory:
    def __init__(self):
        self.saved = None

    def recent(self, user_id, limit):
        return ["turns"]

    def to_messages(self, turns):
        return [{"role": "user", "content": "ran 5k"}, {"role": "assistant", "content": "[actions taken: x]"}]

    def turn_count(self, user_id):
        return 42

    def save_domain_summary(self, user_id, domain, summary, turns_covered):
        self.saved = (domain, summary, turns_covered)


def test_conversation_goes_as_one_user_document():
    groq, history = FakeGroq("They ran 5k."), FakeHistory()
    make_summariser(groq)(uuid4(), "move", history)
    roles = [m["role"] for m in groq.sent["messages"]]
    assert roles == ["system", "user"]
    assert "They: ran 5k" in groq.sent["messages"][1]["content"]
    assert history.saved == ("move", "They ran 5k.", 42)


def test_empty_groq_reply_falls_back_with_the_same_document():
    fallback, history = FakeAnthropic("They ran 5k."), FakeHistory()
    make_summariser(FakeGroq(""), fallback_client=fallback)(uuid4(), "move", history)
    assert [m["role"] for m in fallback.sent["messages"]] == ["user"]
    assert history.saved[1] == "They ran 5k."
