"""The model boundary: the conversation engine and everything above it run
without any provider's client, and the Anthropic connector keeps what the inline
code did — caching breakpoints, message shapes, all text blocks."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from trellis.core_model import SystemPrompt
from trellis.infra_anthropic import AnthropicConnector

SRC = Path(__file__).parent.parent / "src" / "trellis"
PROVIDER_FREE = ("core_model.py", "core_oracle.py", "core_assembler.py")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_engine_and_assembler_import_no_provider_client():
    for name in PROVIDER_FREE:
        assert not {"anthropic", "openai", "groq", "google"} & _imports(SRC / name), name


def test_the_engine_builds_no_provider_shaped_messages():
    for name in ("core_oracle.py", "core_assembler.py"):
        source = (SRC / name).read_text()
        for marker in ("cache_control", '"tool_result"', "tool_use_id", "stop_reason"):
            assert marker not in source, (name, marker)


class _Client:
    def __init__(self, responses):
        self.seen = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.seen.append(kwargs)
        return self._responses.pop(0)


def _resp(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks), usage=None)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def test_stable_parts_become_cache_breakpoints():
    client = _Client([_resp("end_turn", _text("hi"))])
    session = AnthropicConnector(client, "m").open(
        SystemPrompt(stable="constitution", volatile="today's context"),
        [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply", "stable_prefix": True},
         {"role": "user", "content": "now"}],
        tools=[{"name": "t"}])
    session.next()
    sent = client.seen[0]
    assert sent["system"][0] == {"type": "text", "text": "constitution", "cache_control": {"type": "ephemeral"}}
    assert sent["system"][1]["text"].endswith("today's context") and "cache_control" not in sent["system"][1]
    assert sent["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][2] == {"role": "user", "content": "now"}
    assert "stable_prefix" not in str(sent["messages"])          # the neutral marker never reaches the provider


def test_tool_results_go_back_in_anthropics_shape_with_the_note_last():
    from trellis.core_model import ToolOutcome
    tool_use = SimpleNamespace(type="tool_use", id="u1", name="save", input={"x": 1})
    client = _Client([_resp("tool_use", _text("On it."), tool_use), _resp("end_turn", _text("Done."))])
    session = AnthropicConnector(client, "m").open("sys", [{"role": "user", "content": "q"}], [{"name": "save"}])
    reply = session.next()
    assert reply.text == "On it." and not reply.finished
    assert [(r.id, r.name, r.input) for r in reply.tool_requests] == [("u1", "save", {"x": 1})]
    session.give_tool_results([ToolOutcome(reply.tool_requests[0], "Saved.")], note="their message")
    assert session.next().finished
    back = client.seen[1]["messages"][-1]["content"]
    assert back[0] == {"type": "tool_result", "tool_use_id": "u1", "content": "Saved."}
    assert back[-1] == {"type": "text", "text": "their message"}


def test_complete_picks_the_small_model_for_background_work():
    client = _Client([_resp("end_turn", _text("a"), _text("b")), _resp("end_turn", _text("c"))])
    connector = AnthropicConnector(client, "big", small_model="small")
    assert connector.complete("s", "u", max_tokens=100) == "a\n\nb"
    connector.complete("s", "u", max_tokens=100, tier="small")
    assert [k["model"] for k in client.seen] == ["big", "small"]
