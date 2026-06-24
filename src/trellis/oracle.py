from __future__ import annotations

import logging
from typing import Callable

from anthropic import Anthropic

_log = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 8


class Oracle:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def run(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        handlers: dict[str, Callable[[dict], str]],
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": 8192,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = None
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(**kwargs)

            if response.stop_reason == "end_turn":
                return self._extract_text(response)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._call(block.name, block.input, handlers)
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

            return self._extract_text(response)

        _log.warning("oracle hit iteration cap")
        return self._extract_text(response) if response else ""

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
        return ""
