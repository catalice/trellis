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


class FakeModel:
    """A core_model connector's one-shot side."""
    def __init__(self, reply):
        self.reply, self.sent = reply, None

    def complete(self, system, user, *, max_tokens, tier="main"):
        self.sent = {"system": system, "user": user, "tier": tier}
        return self.reply


class FakeHistory:
    def __init__(self):
        self.saved = None

    def recent(self, user_id, limit):
        return ["turns"]

    def to_messages(self, turns):
        return [{"role": "user", "content": "ran 5k"}, {"role": "assistant", "content": "[actions taken: x]"}]

    def turn_count(self, user_id):
        return 42

    def domain_summary(self, user_id, domain):
        return None

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
    fallback, history = FakeModel("They ran 5k."), FakeHistory()
    make_summariser(FakeGroq(""), fallback=fallback)(uuid4(), "move", history)
    assert "They: ran 5k" in fallback.sent["user"]          # the whole conversation, as one document
    assert fallback.sent["tier"] == "small"                  # background work uses the small model
    assert history.saved[1] == "They ran 5k."


class _HistoryWithARecord(FakeHistory):
    def domain_summary(self, user_id, domain):
        from datetime import datetime, timezone
        return ("They asked whether to move the long run; left open.", datetime(2026, 3, 1, tzinfo=timezone.utc))


def test_the_earlier_record_is_read_so_open_threads_are_carried_not_overwritten():
    """Each summary replaced the last without reading it: a thread left open fell
    out of the newest 40 turns and was gone."""
    groq, history = FakeGroq("They ran 5k. Still open: whether to move the long run."), _HistoryWithARecord()
    make_summariser(groq)(uuid4(), "move", history)
    document = groq.sent["messages"][1]["content"]
    assert "They asked whether to move the long run; left open." in document     # the model can see it
    assert document.index("Earlier record") < document.index("They: ran 5k")     # older first, then what's new
    assert "still open" in groq.sent["messages"][0]["content"].lower()           # and is told what to do with it
    assert history.saved[1] == "They ran 5k. Still open: whether to move the long run."


def test_with_no_earlier_record_the_document_is_just_the_transcript():
    groq, history = FakeGroq("They ran 5k."), FakeHistory()
    make_summariser(groq)(uuid4(), "move", history)
    assert "Earlier record" not in groq.sent["messages"][1]["content"]


class _HistoryThatCannotBeRead(FakeHistory):
    def domain_summary(self, user_id, domain):
        raise ConnectionError("database went away")


def test_an_unreadable_earlier_record_is_kept_not_replaced():
    """A failed read was treated as 'nothing stored': a summary written without
    the record then replaced it, and the open threads went with it."""
    groq, history = FakeGroq("They ran 5k."), _HistoryThatCannotBeRead()
    make_summariser(groq)(uuid4(), "move", history)
    assert history.saved is None                     # the stored record stands
    assert not getattr(groq, "sent", None)           # and no model call was spent on it
