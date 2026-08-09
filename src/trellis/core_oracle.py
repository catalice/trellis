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
_TRACE_RESULT_CHARS = 200  # must fit a confirmation label + its 36-char id (120 chopped ids — audit item 25)

# The ANSWER CHECK (audit item 29): prompt lines reliably lose to tool-momentum
# on this model — three live failures of "did the work, ignored the questions".
# So it's a gate, not an instruction: when a turn used tools AND her message
# asked a question, the draft doesn't ship until one tiny standalone call
# confirms every question is answered (or rewrites so it is). A few hundred
# tokens, no tools, only on the turn-shape that keeps failing. Scaffolding
# around a model weakness — remove it the day a model holds the instruction.
_ANSWER_CHECK_SYSTEM = """\
You review a draft reply from Trellis (a personal assistant on Telegram).
You are given THEIR MESSAGE, the ACTIONS taken this turn, and the DRAFT reply.

If the draft genuinely answers EVERY question in their message, return the
draft EXACTLY as it is — change nothing.

If any question is unanswered, rewrite the reply: answer every question first,
in Trellis's warm plain voice with its reasoning, THEN the brief report of
actions taken. Keep it concise. Plain text, no tables, no headers.

Return ONLY the final reply text — no commentary, no quotes around it.\
"""


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
        last = messages[-1]["content"] if messages else ""
        user_message = last if isinstance(last, str) else ""
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
                return self._answer_checked(user_message, self._finish(response, calls))

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

            return self._answer_checked(user_message, self._finish(response, calls))

        _log.warning("oracle hit iteration cap")
        return self._answer_checked(user_message, self._finish(response, calls))

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

    def _answer_checked(self, user_message: str, result: OracleResult) -> OracleResult:
        """The gate: tool-turn + question in her message -> one tiny review call.
        Best-effort — any failure ships the draft unchanged."""
        if not result.tool_calls or not result.text or "?" not in user_message:
            return result
        try:
            actions = "; ".join(
                f"{c.name}: {c.result_summary}" for c in result.tool_calls
            ) or "(none)"
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4000,
                system=_ANSWER_CHECK_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"THEIR MESSAGE:\n{user_message}\n\n"
                        f"ACTIONS TAKEN:\n{actions}\n\n"
                        f"DRAFT REPLY:\n{result.text}"
                    ),
                }],
            )
            checked = "\n\n".join(
                b.text.strip() for b in response.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", "").strip()
            )
            if checked and checked != result.text:
                _log.info("oracle: answer check rewrote the reply")
            return OracleResult(checked or result.text, result.tool_calls)
        except Exception:
            _log.warning("oracle: answer check failed — shipping draft", exc_info=True)
            return result

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
