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
            "max_tokens": 8192,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        calls: list[ToolCall] = []
        response = None
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._api_call(kwargs)

            if response.stop_reason == "end_turn":
                return OracleResult(self._extract_text(response), tuple(calls))

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

            return OracleResult(self._extract_text(response), tuple(calls))

        _log.warning("oracle hit iteration cap")
        text = self._extract_text(response) if response else ""
        if not text:
            _log.warning("oracle returned empty text at iteration cap; last stop_reason=%s", getattr(response, "stop_reason", None))
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
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        _log.warning(
            "oracle: no text block in response; stop_reason=%s content_types=%s",
            getattr(response, "stop_reason", None),
            [getattr(b, "type", type(b).__name__) for b in response.content],
        )
        return ""
