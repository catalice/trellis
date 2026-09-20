"""
The scenario harness: one set of scenarios, simulated tools and outcome checks,
run two ways.

  scripted model   deterministic. Proves what the SOFTWARE guarantees — given
                   this model behaviour and these tool results, what reaches
                   the person, and what was done.
  real model       an evaluation. Shows how well a MODEL behaves with the same
                   message, tools and failures. Costs money and varies run to
                   run, so it is opt-in (see test_scenarios.py).

Both run the real conversation engine. A scenario never mentions a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from trellis.core_model import ModelReply, SystemPrompt, ToolOutcome, ToolRequest
from trellis.core_oracle import Oracle, OracleResult


# --- the scripted model --------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One thing the scripted model does: say something, ask for tools, or both.
    A step with no tools ends the model's turn."""
    text: str = ""
    tools: tuple[tuple[str, dict], ...] = ()


class ScriptedModel:
    """A core_model connector that plays a fixed script and records everything
    the engine hands it."""

    def __init__(self, steps: list[Step], completions: list[str] | None = None) -> None:
        self._steps = list(steps)
        self._completions = list(completions or [])
        self.opened_with: dict | None = None
        self.results_given: list[list[ToolOutcome]] = []
        self.notes: list[str | None] = []
        self.said: list[str] = []

    def open(self, system, turns, tools):
        self.opened_with = {"system": system, "turns": turns, "tools": tools}
        return self

    def next(self) -> ModelReply:
        if not self._steps:
            raise AssertionError("the engine asked the scripted model for more steps than the script has")
        step = self._steps.pop(0)
        requests = tuple(ToolRequest(id=f"call-{len(self.results_given)}-{i}", name=name, input=dict(args))
                         for i, (name, args) in enumerate(step.tools))
        return ModelReply(text=step.text, tool_requests=requests, finished=not requests)

    def give_tool_results(self, outcomes, note=None) -> None:
        self.results_given.append(list(outcomes))
        self.notes.append(note)

    def say(self, text: str) -> None:
        self.said.append(text)

    def complete(self, system, user, *, max_tokens, tier="main") -> str:
        return self._completions.pop(0) if self._completions else ""

    @property
    def steps_left(self) -> int:
        return len(self._steps)


# --- simulated tools --------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """One ATTEMPT. Recorded before the handler runs, so an attempt that raises
    part-way — after it has already changed something — is still on record."""
    name: str
    input: dict
    result: str | None = None           # what the handler returned, if it returned
    raised: BaseException | None = None  # what it raised, if it raised


class SimulatedTools:
    """Tool definitions plus handlers whose behaviour a scenario controls: a
    fixed reply, a function of the input, or an exception (a handler that
    crashes)."""

    def __init__(self, tools: dict[str, tuple[dict, object]]) -> None:
        self._tools = tools
        self.calls: list[ToolCallRecord] = []

    @property
    def schemas(self) -> list[dict]:
        return [schema for schema, _ in self._tools.values()]

    @property
    def handlers(self) -> dict[str, Callable[[dict], str]]:
        return {name: self._handler(name, behaviour) for name, (_, behaviour) in self._tools.items()}

    def _handler(self, name: str, behaviour) -> Callable[[dict], str]:
        def handle(input_dict: dict) -> str:
            record = ToolCallRecord(name, dict(input_dict))
            self.calls.append(record)                    # the attempt, before anything can go wrong
            try:
                if isinstance(behaviour, BaseException):
                    raise behaviour
                # A str is returned as it is — str() would strip an ActionResult's status.
                record.result = behaviour(input_dict) if callable(behaviour) else behaviour
            except BaseException as error:
                record.raised = error
                raise
            return record.result
        return handle


def tool(name: str, description: str, **properties: str) -> dict:
    """A tool definition in Trellis's own shape."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {key: {"type": "string", "description": text} for key, text in properties.items()},
            "required": list(properties),
        },
    }


# --- scenarios and outcomes --------------------------------------------------------

@dataclass
class Outcome:
    reply: str
    calls: list[ToolCallRecord]
    result: OracleResult
    model: object                       # the connector used — a ScriptedModel exposes what it was handed

    def called(self, name: str) -> list[ToolCallRecord]:
        return [c for c in self.calls if c.name == name]


Check = Callable[[Outcome], None]


@dataclass
class Scenario:
    name: str
    message: str
    tools: dict[str, tuple[dict, object]]
    script: list[Step]                  # what the scripted model does
    checks: list[Check]                 # hold in BOTH modes — about outcomes, never about the script
    scripted_checks: list[Check] = field(default_factory=list)   # engine guarantees; scripted mode only
    real_model: bool = True             # False: only meaningful against a script (e.g. a model that goes silent)
    context: str = "Today: Monday 2 March 2026, 09:00."


def run(scenario: Scenario, model=None, system_base: str | None = None, read_only=frozenset()) -> Outcome:
    """Run one scenario through the real conversation engine. No model given =
    the scenario's script."""
    from trellis.core_assembler import _SYSTEM_BASE
    connector = model or ScriptedModel(scenario.script)
    tools = SimulatedTools(scenario.tools)
    result = Oracle(connector).run(
        SystemPrompt(stable=system_base or _SYSTEM_BASE, volatile=scenario.context),
        [{"role": "user", "content": scenario.message}],
        tools.schemas,
        tools.handlers,
        read_only=read_only,
    )
    return Outcome(reply=result.text, calls=tools.calls, result=result, model=connector)
