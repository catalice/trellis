from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from trellis.core_actions import (
    ActionLog, ActionRecord, InMemoryActionLog, Status, failed, receipt, status_of, unknown,
)
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
    actions: tuple[ActionRecord, ...] = ()      # what was attempted and how each went — from the record

    def trace(self) -> str | None:
        """Compact record of actions taken, for conversation history."""
        if not self.tool_calls:
            return None
        marks = {a_id: a.status for a_id, a in enumerate(self.actions)}
        entries = "; ".join(
            f"{c.name} → " + (f"{marks[i].upper()}: " if marks.get(i, Status.SUCCEEDED) is not Status.SUCCEEDED else "")
            + c.result_summary
            for i, c in enumerate(self.tool_calls))
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
        actions: ActionLog | None = None,       # where attempts are recorded; in memory if none given
    ) -> OracleResult:
        log = actions or InMemoryActionLog()
        done: list[ActionRecord] = []
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
                # Also when something did NOT cleanly succeed and the model ended
                # without a word about it — whatever it said earlier in the turn.
                unsaid = not spoken or any(a.status is not Status.SUCCEEDED for a in done)
                if calls and not nudged and unsaid and not reply.text:
                    nudged = True
                    _log.warning("oracle: silent end_turn after tools — nudging")
                    session.say(
                        "[Trellis internal: your turn ended without a message. "
                        "The user has seen NOTHING — tool results never reach "
                        "them. Reply now in your own words, addressing "
                        "everything they said.]"
                    )
                    continue
                return self._finish(reply, calls, spoken, done)

            if reply.wants_tools:
                if reply.text:
                    spoken.append(reply.text)
                outcomes = []
                for request in reply.tool_requests:
                    result = self._attempt(request.name, request.input, handlers, log, done)
                    if result is None:          # a blind retry, refused — not an action, not in the trace
                        outcomes.append(ToolOutcome(request=request, content=_NOT_RETRIED))
                        continue
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

            return self._finish(reply, calls, spoken, done)

        _log.warning("oracle hit iteration cap")
        return self._finish(reply, calls, spoken, done)

    def _finish(
        self, reply: ModelReply | None, calls: list[ToolCall], spoken: list[str] | None = None,
        done: list[ActionRecord] | None = None,
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
        # The reply is assembled against the RECORD, not the model's account of
        # it. Whatever the model wrote — before a tool failed, after it, or
        # nothing at all — anything that did not cleanly succeed is stated here,
        # last, in the record's words. A clean turn adds nothing.
        correction = receipt(done or [])
        if correction:
            text = f"{text}\n\n{correction}" if text else correction
        return OracleResult(text, tuple(calls), tuple(done or ()))

    def _attempt(
        self, name: str, input_dict: dict, handlers: dict[str, Callable[[dict], str]],
        log: ActionLog, done: list[ActionRecord],
    ) -> str | None:
        """Run one requested action, on the record. None = refused as a blind
        retry: the same action, same input, whose earlier outcome this turn is
        UNKNOWN — running it again could do it twice."""
        if any(a.tool == name and a.input == input_dict and a.status is Status.UNKNOWN for a in done):
            _log.warning("oracle: refused a blind retry of %s after an unknown outcome", name)
            return None
        handle = None
        try:
            handle = log.begin(name, input_dict)             # BEFORE it runs
        except Exception:
            _log.warning("action log begin failed", exc_info=True)
        result = self._call(name, input_dict, handlers)
        status = status_of(result)
        summary = result.splitlines()[0][:_TRACE_RESULT_CHARS] if result else ""
        record = handle if isinstance(handle, ActionRecord) else ActionRecord(tool=name, input=dict(input_dict))
        record.status, record.summary = status, summary
        done.append(record)
        if handle is not None:
            try:
                log.finish(handle, status, summary)
            except Exception:
                _log.warning("action log finish failed", exc_info=True)
        return result

    def _call(self, name: str, input_dict: dict, handlers: dict[str, Callable[[dict], str]]) -> str:
        handler = handlers.get(name)
        if handler is None:
            _log.warning("unknown tool called: %s", name)
            return failed(f"Tool '{name}' not available.")
        try:
            return handler(input_dict)
        except Exception:
            _log.exception("tool %s failed", name)
            return unknown(_OUTCOME_UNKNOWN)


# An exception is not a failure: the action may have taken effect before it
# raised. The old text here said "try again in a moment" — and a real model did,
# blind (first evaluation run, 20 Sep 2026).
_OUTCOME_UNKNOWN = (
    "OUTCOME UNKNOWN — this action hit an error part-way. It may or may not have "
    "taken effect. Do NOT run it again. If a read tool can show whether it "
    "happened, check; then tell them plainly what you know and what you don't."
)
_NOT_RETRIED = (
    "Not retried: this same action's earlier attempt this turn has an unknown "
    "outcome, and running it again could do it twice. Check what is stored "
    "instead, and tell them where things stand."
)
