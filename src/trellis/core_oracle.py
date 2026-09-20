from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from trellis.core_model import ModelConnector, ModelReply, SystemPrompt, ToolOutcome, Turn

_log = logging.getLogger(__name__)

# The conversation engine: one turn, tools until the model is done. It speaks
# core_model only — which provider answers is the connector's business.
_MAX_TOOL_ITERATIONS = 8
_TRACE_RESULT_CHARS = 200  # must fit a confirmation label + its 36-char id (120 chopped ids — audit item 25)

# DELIVERY CONTRACT: everything the model writes in a turn reaches the user.
# The model often answers FIRST and calls a tool second, in the same step. That
# text used to be dropped — only the final step's text shipped — so the model
# saw its answer in history, the user never got it, and the follow-up read
# "as I laid out above". Every step's text is collected and delivered, in order.
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
    def __init__(self, model: ModelConnector) -> None:
        self._model = model

    def run(
        self,
        system: SystemPrompt | str,
        messages: list[Turn],
        tools: list[dict],
        handlers: dict[str, Callable[[dict], str]],
    ) -> OracleResult:
        last = messages[-1]["content"] if messages else ""
        user_message = last if isinstance(last, str) else ""
        session = self._model.open(system, messages, tools)

        calls: list[ToolCall] = []
        spoken: list[str] = []   # text from every step, in order — all of it ships
        reply: ModelReply | None = None
        nudged = False
        for _ in range(_MAX_TOOL_ITERATIONS):
            reply = session.next()

            if reply.finished:
                # The NUDGE: a silent end after tool calls means the model
                # decided the results speak for themselves — they don't (tool
                # results never reach the user). ONE follow-up asks it to
                # speak; the deterministic fallback in _finish stays as backstop.
                if calls and not nudged and not spoken and not reply.text:
                    nudged = True
                    _log.warning("oracle: silent end_turn after tools — nudging")
                    session.say(
                        "[Trellis internal: your turn ended without a message. "
                        "The user has seen NOTHING — tool results never reach "
                        "them. Reply now in your own words, addressing "
                        "everything they said.]"
                    )
                    continue
                return self._finish(reply, calls, spoken)

            if reply.wants_tools:
                if reply.text:
                    spoken.append(reply.text)
                outcomes = []
                for request in reply.tool_requests:
                    result = self._call(request.name, request.input, handlers)
                    calls.append(ToolCall(
                        name=request.name,
                        result_summary=result.splitlines()[0][:_TRACE_RESULT_CHARS] if result else "",
                    ))
                    outcomes.append(ToolOutcome(request=request, content=result))
                # The user's idea (item 29b): the not-answering failure is DISTANCE —
                # by reply time their message is buried under tool results and
                # recency wins. So their message rides right behind every round
                # of results: when the model writes, their questions are the
                # LAST thing it read. Prevention: their message is the last
                # thing read before writing. (The answer-check gate this
                # backstopped was retired 2 Sep - it began degrading replies.)
                note = None
                if user_message:
                    note = (
                        "[reminder — their message this turn, answer every "
                        f"part of it when you reply: \"{user_message}\". "
                        "Anything you wrote before calling tools WILL reach "
                        "them together with what you write now: don't repeat "
                        "it and don't point them at it — continue from it.]"
                    )
                session.give_tool_results(outcomes, note)
                continue

            return self._finish(reply, calls, spoken)

        _log.warning("oracle hit iteration cap")
        return self._finish(reply, calls, spoken)

    def _finish(
        self, reply: ModelReply | None, calls: list[ToolCall], spoken: list[str] | None = None,
    ) -> OracleResult:
        """Final result for the turn: every earlier step's text, in order, then
        the final step's. An exact repeat of an earlier step is dropped (the
        model occasionally restates itself after a tool). The model sometimes
        ends with no text at all after tool calls (deciding the results speak
        for themselves); the tools DID run, so that must never surface as a
        failure. Fall back to the tool results — handlers return confirmations."""
        parts: list[str] = list(spoken or [])
        final = reply.text if reply else ""
        if final and final not in parts:
            parts.append(final)
        text = "\n\n".join(parts)
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
