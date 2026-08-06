from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import anthropic
from anthropic import Anthropic

_log = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 8
_RETRY_DELAYS = (1.0, 3.0)  # two retries, exponential-ish
_TRACE_RESULT_CHARS = 120


@dataclass(frozen=True)
class ToolCall:
    name: str
    result_summary: str  # first line of the tool result, truncated


@dataclass(frozen=True)
class OracleResult:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()

    def trace(self) -> str | None:
        """Compact record of actions taken, for conversation history."""
        if not self.tool_calls:
            return None
        entries = "; ".join(f"{c.name} → {c.result_summary}" for c in self.tool_calls)
        return f"[actions taken: {entries}]"


class Oracle:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def _api_call(self, kwargs: dict):
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

    def run(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        handlers: dict[str, Callable[[dict], str]],
    ) -> OracleResult:
        kwargs: dict = {
            "model": self._model,
            # On Sonnet 5 adaptive thinking is on by default and max_tokens caps
            # thinking + reply TOGETHER — 8192 could truncate a reply mid-thought.
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        calls: list[ToolCall] = []
        response = None
        nudged = False
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._api_call(kwargs)

            if response.stop_reason == "end_turn":
                # The NUDGE: a silent end_turn after tool calls means the model
                # decided the results speak for themselves — they don't (tool
                # results never reach the user). ONE follow-up call asks it to
                # speak; the deterministic fallback in _finish stays as backstop.
                if calls and not nudged and not self._extract_text(response):
                    nudged = True
                    _log.warning("oracle: silent end_turn after tools — nudging")
                    followup: list[dict] = list(kwargs["messages"])
                    if response.content:
                        followup.append({"role": "assistant", "content": response.content})
                    followup.append({
                        "role": "user",
                        "content": (
                            "[Trellis internal: your turn ended without a message. "
                            "The user has seen NOTHING — tool results never reach "
                            "them. Reply now in your own words, addressing "
                            "everything they said.]"
                        ),
                    })
                    kwargs["messages"] = followup
                    continue
                return self._finish(response, calls)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._call(block.name, block.input, handlers)
                        calls.append(ToolCall(
                            name=block.name,
                            result_summary=result.splitlines()[0][:_TRACE_RESULT_CHARS] if result else "",
                        ))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                kwargs["messages"] = [
                    *kwargs["messages"],
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": tool_results},
                ]
                continue

            return self._finish(response, calls)

        _log.warning("oracle hit iteration cap")
        return self._finish(response, calls)

    def _finish(self, response, calls: list[ToolCall]) -> OracleResult:
        """Final result for the turn. The model occasionally ends its turn with
        no text after tool calls (deciding the results speak for themselves);
        the tools DID run, so that must never surface as a failure. Fall back
        to the tool results — handlers return user-facing confirmations."""
        text = self._extract_text(response) if response else ""
        if not text and calls:
            text = " ".join(c.result_summary for c in calls if c.result_summary)
            _log.warning(
                "oracle: empty final text after %d tool call(s); replying with the tool results",
                len(calls),
            )
        return OracleResult(text, tuple(calls))

    def _call(self, name: str, input_dict: dict, handlers: dict[str, Callable[[dict], str]]) -> str:
        handler = handlers.get(name)
        if handler is None:
            _log.warning("unknown tool called: %s", name)
            return f"Tool '{name}' not available."
        try:
            return handler(input_dict)
        except Exception:
            _log.exception("tool %s failed", name)
            return "Something went wrong with that action — try again in a moment."

    @staticmethod
    def _extract_text(response) -> str:
        """ALL text blocks, joined — a response can carry several (e.g. text
        around tool use); taking only the first silently drops the rest."""
        texts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", "")
        ]
        if not texts:
            return ""
        return "\n\n".join(t.strip() for t in texts if t.strip())
