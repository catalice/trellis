"""
The Anthropic connector — the one module that knows Anthropic's client, message
shapes, prompt caching, token limits and errors. Everything else speaks
core_model.

Behaviour is what core_oracle did inline before the boundary existed: the same
retries, the same caching breakpoints (stable system text + tools, and the
stable history prefix), the same token ceiling.
"""
from __future__ import annotations

import logging
import time

import anthropic
from anthropic import Anthropic

from trellis.core_model import ModelReply, SystemPrompt, ToolOutcome, ToolRequest, Turn

_log = logging.getLogger(__name__)

_RETRY_DELAYS = (1.0, 3.0)  # two retries, exponential-ish
_CACHE = {"type": "ephemeral"}
# On Sonnet 5 adaptive thinking is on by default and max_tokens caps thinking +
# reply TOGETHER — 8192 could truncate a reply mid-thought.
_TURN_MAX_TOKENS = 16000


class AnthropicConnector:
    def __init__(self, client: Anthropic, model: str, small_model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._small_model = small_model or model

    @classmethod
    def from_key(cls, api_key: str, model: str, small_model: str | None = None) -> "AnthropicConnector":
        return cls(Anthropic(api_key=api_key), model, small_model)

    # -- the tool-using exchange ------------------------------------------------

    def open(self, system: SystemPrompt | str, turns: list[Turn], tools: list[dict]) -> "_Session":
        kwargs: dict = {
            "model": self._model,
            "max_tokens": _TURN_MAX_TOKENS,
            "system": _system_blocks(system),
            "messages": [_message(t) for t in turns],
        }
        if tools:
            kwargs["tools"] = tools
        return _Session(self, kwargs)

    # -- one question, one answer -----------------------------------------------

    def complete(self, system: str, user: str, *, max_tokens: int, tier: str = "main") -> str:
        response = self._call({
            "model": self._small_model if tier == "small" else self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        })
        return _text(response)

    # -- transport ----------------------------------------------------------------

    def _call(self, kwargs: dict):
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                return self._client.messages.create(**kwargs)
            except (
                anthropic.RateLimitError,
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
            ) as exc:
                last_exc = exc
                _log.warning("Anthropic API transient error (attempt %d): %s", attempt + 1, exc)
                if delay is not None:
                    time.sleep(delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code in (529, 503, 500) and delay is not None:
                    last_exc = exc
                    _log.warning("Anthropic API %d (attempt %d): %s", exc.status_code, attempt + 1, exc)
                    time.sleep(delay)
                else:
                    raise
        raise last_exc  # type: ignore[misc]


class _Session:
    """The provider-side conversation for one turn."""

    def __init__(self, connector: AnthropicConnector, kwargs: dict) -> None:
        self._connector = connector
        self._kwargs = kwargs
        self._last_content = None       # the assistant content of the latest reply, as Anthropic sent it
        self._logged_cache = False

    def next(self) -> ModelReply:
        response = self._connector._call(self._kwargs)
        if not self._logged_cache:
            self._logged_cache = True
            u = getattr(response, "usage", None)
            if u is not None:
                _log.info(
                    "oracle usage: in=%s cache_read=%s cache_write=%s",
                    getattr(u, "input_tokens", "?"),
                    getattr(u, "cache_read_input_tokens", 0),
                    getattr(u, "cache_creation_input_tokens", 0),
                )
        self._last_content = response.content
        requests = ()
        if response.stop_reason == "tool_use":
            requests = tuple(
                ToolRequest(id=block.id, name=block.name, input=block.input)
                for block in response.content if getattr(block, "type", None) == "tool_use"
            )
        return ModelReply(
            text=_text(response),
            tool_requests=requests,
            finished=response.stop_reason == "end_turn",
        )

    def give_tool_results(self, outcomes: list[ToolOutcome], note: str | None = None) -> None:
        content: list = [
            {"type": "tool_result", "tool_use_id": o.request.id, "content": o.content}
            for o in outcomes
        ]
        if note:
            content.append({"type": "text", "text": note})
        self._kwargs["messages"] = [
            *self._kwargs["messages"],
            {"role": "assistant", "content": self._last_content},
            {"role": "user", "content": content},
        ]

    def say(self, text: str) -> None:
        messages = list(self._kwargs["messages"])
        if self._last_content:
            messages.append({"role": "assistant", "content": self._last_content})
        messages.append({"role": "user", "content": text})
        self._kwargs["messages"] = messages


def _system_blocks(system: SystemPrompt | str):
    """The stable text is a cache breakpoint: with the tool definitions ahead of
    it, it caches at a tenth of the price; the volatile context is the tail."""
    if isinstance(system, str):
        return system
    blocks = [{"type": "text", "text": system.stable, "cache_control": _CACHE}]
    if system.volatile:
        blocks.append({"type": "text", "text": f"---\n\n{system.volatile}"})
    return blocks


def _message(turn: Turn) -> dict:
    """History is append-only within the day, so its prefix is stable — the
    turn marked stable_prefix becomes the second cache breakpoint."""
    content = turn["content"]
    if turn.get("stable_prefix") and isinstance(content, str):
        content = [{"type": "text", "text": content, "cache_control": _CACHE}]
    return {"role": turn["role"], "content": content}


def _text(response) -> str:
    """ALL text blocks, joined — a response can carry several (e.g. text around
    tool use); taking only the first silently drops the rest."""
    texts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", "")
    ]
    if not texts:
        return ""
    return "\n\n".join(t.strip() for t in texts if t.strip())
