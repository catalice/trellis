"""
Action outcomes — what happened when a tool ran, independent of any model.

A tool handler returns text for the model to read. That text now carries HOW the
action went: `failed("…")`, `partial("…")`, or a plain string, which means it
succeeded (every handler written before this returns plain strings, and keeps
working). An exception is never "failed": the action may have taken effect
before it raised, so its outcome is UNKNOWN.

The record of what was attempted is written before the handler runs and closed
after. It belongs to the turn, not to the model's account of the turn: a model
response that is interrupted, wrong or silent cannot erase it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol


class Status(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"       # some of it happened; the text says what didn't
    FAILED = "failed"         # nothing changed
    UNKNOWN = "unknown"       # it may or may not have happened — never retried blind


class ActionResult(str):
    """The text a handler returns, carrying its status. A str, so everything
    that treats tool results as text keeps working."""
    status: Status

    def __new__(cls, text: str, status: Status = Status.SUCCEEDED) -> "ActionResult":
        result = super().__new__(cls, text)
        result.status = status
        return result


def failed(text: str) -> ActionResult:
    """Nothing changed."""
    return ActionResult(text, Status.FAILED)


def partial(text: str) -> ActionResult:
    """Some of it happened — say what didn't."""
    return ActionResult(text, Status.PARTIAL)


def unknown(text: str) -> ActionResult:
    """It may or may not have taken effect (a timeout, a lost acknowledgement)."""
    return ActionResult(text, Status.UNKNOWN)


def status_of(result: object) -> Status:
    return getattr(result, "status", Status.SUCCEEDED)


@dataclass
class ActionRecord:
    tool: str
    input: dict
    status: Status = Status.UNKNOWN          # until closed: an attempt nobody finished is unknown
    summary: str = ""                         # first line of what the handler said
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class ActionLog(Protocol):
    """Where a turn's attempts are recorded. `begin` is called BEFORE the
    handler runs; `finish` after. Implementations must not raise — a failed
    record must never block or break the action itself."""
    def begin(self, tool: str, input: dict) -> object: ...
    def finish(self, handle: object, status: Status, summary: str) -> None: ...


class InMemoryActionLog:
    def __init__(self) -> None:
        self.entries: list[ActionRecord] = []

    def begin(self, tool: str, input: dict) -> ActionRecord:
        record = ActionRecord(tool=tool, input=dict(input))
        self.entries.append(record)
        return record

    def finish(self, handle: ActionRecord, status: Status, summary: str) -> None:
        handle.status, handle.summary = status, summary
        handle.finished_at = datetime.now(timezone.utc)


def receipt(actions: list[ActionRecord] | tuple[ActionRecord, ...]) -> str:
    """What the person must be told whatever the model wrote: every action that
    did not cleanly succeed, from the record. Empty when all went well."""
    lines = []
    for action in actions:
        if action.status is Status.FAILED:
            lines.append(f"⚠️ Not done: {action.summary or action.tool}")
        elif action.status is Status.PARTIAL:
            lines.append(f"⚠️ Partly done: {action.summary or action.tool}")
        elif action.status is Status.UNKNOWN:
            lines.append(
                f"⚠️ Not confirmed: {action.tool} hit an error part-way. It may or may not "
                "have gone through — I haven't retried it."
            )
    return "\n".join(lines)
