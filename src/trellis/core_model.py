"""
The model boundary — what Trellis needs from a language model, in its own terms.

Knows about: a system prompt, conversation turns, tool requests and results.
Does NOT know about: any provider's client, message format, caching or errors.

The conversation engine (core_oracle) and everything above it speak only these
types. A connector (infra_anthropic today) translates them for one provider and
owns that provider's state, caching and retries. A scripted connector drives the
test harness. An action means the same whichever model asked for it.

Tool definitions keep Trellis's own shape — {"name", "description",
"input_schema"} with a JSON-schema body; a connector translates if its provider
wants another.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SystemPrompt:
    """`stable` never changes between turns (a connector may cache it, with the
    tool definitions); `volatile` is this turn's context."""
    stable: str
    volatile: str = ""


# A conversation turn is {"role": "user"|"assistant", "content": str}. One turn
# may carry "stable_prefix": True — everything up to and including it is
# unchanged since the last call, which a connector may use for caching.
Turn = dict


@dataclass(frozen=True)
class ToolRequest:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolOutcome:
    request: ToolRequest
    content: str            # what the tool handler returned, for the model to read


@dataclass(frozen=True)
class ModelReply:
    text: str                                   # everything the model wrote in this step
    tool_requests: tuple[ToolRequest, ...] = ()
    finished: bool = True                       # the model ended its turn (vs. asked for tools / was cut off)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_requests)


class ModelSession(Protocol):
    """One turn's exchange with a model. The connector holds the provider-side
    conversation; the engine only says what happened next."""

    def next(self) -> ModelReply:
        """Ask the model for its next step."""
        ...

    def give_tool_results(self, outcomes: list[ToolOutcome], note: str | None = None) -> None:
        """Hand back the results of the tools it asked for, with an optional
        note for it to read last."""
        ...

    def say(self, text: str) -> None:
        """Add a message from Trellis's side (not the user's) before the next step."""
        ...


class ModelConnector(Protocol):
    def open(self, system: SystemPrompt | str, turns: list[Turn], tools: list[dict]) -> ModelSession:
        """Begin a tool-using exchange."""
        ...

    def complete(self, system: str, user: str, *, max_tokens: int, tier: str = "main") -> str:
        """One question, one answer, no tools — synthesis, discovery, summaries.
        `tier` picks the model: 'main', or 'small' for cheap background work.
        Returns the text ('' if the model said nothing); raises on failure."""
        ...

