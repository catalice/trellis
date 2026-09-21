"""
Action outcomes — what happened when a tool ran, independent of any model.

A tool handler returns text for the model to read. That text carries HOW the
action went: `done("…")`, `failed("…")`, `refused("…")`, `partial("…")`,
`unknown("…")`. A tool that CHANGES something must say it succeeded: a plain
string from one is a refusal or a validation message until proven otherwise, and
is recorded as not done. (It used to read as success — and "Removed that
preference." shipped after "That rule_id isn't a valid id.") A read-only tool may
answer in plain text. An exception is never "failed": the action may have taken
effect before it raised, so its outcome is UNKNOWN.

The record of what was attempted is written before the handler runs and closed
after. It belongs to the turn, not to the model's account of the turn: a model
response that is interrupted, wrong or silent cannot erase it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

_log = logging.getLogger(__name__)


class Status(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"       # some of it happened; the text says what didn't
    FAILED = "failed"         # nothing changed
    UNKNOWN = "unknown"       # it may or may not have happened — never retried blind


class ActionResult(str):
    """The text a handler returns, carrying its status. A str, so everything
    that treats tool results as text keeps working."""
    status: Status
    correctable: bool
    show: str | None        # text that must reach the person WORD FOR WORD (see done())

    def __new__(cls, text: str, status: Status = Status.SUCCEEDED, correctable: bool = False,
                show: str | None = None) -> "ActionResult":
        result = super().__new__(cls, text)
        result.status = status
        result.correctable = correctable
        result.show = show
        return result


def done(text: str, show: str | None = None) -> ActionResult:
    """It happened. A tool that changes something must say so explicitly.
    `show`: when the person is about to DECIDE on something, what they decide on
    can't be the model's retelling of it. The engine puts `show` in the reply
    itself, after the model's words, unaltered — so what they agree to is what
    is held."""
    return ActionResult(text, Status.SUCCEEDED, show=show)


def failed(text: str) -> ActionResult:
    """Nothing changed."""
    return ActionResult(text, Status.FAILED)


def refused(text: str) -> ActionResult:
    """Nothing changed because the REQUEST wasn't acceptable as sent (too long,
    a missing field). The same request, corrected, may follow and resolve it."""
    return ActionResult(text, Status.FAILED, correctable=True)


def partial(text: str) -> ActionResult:
    """Some of it happened — say what didn't."""
    return ActionResult(text, Status.PARTIAL)


def unknown(text: str) -> ActionResult:
    """It may or may not have taken effect (a timeout, a lost acknowledgement)."""
    return ActionResult(text, Status.UNKNOWN)


def status_of(result: object, *, changes_things: bool = True) -> Status:
    """A declared status wins. Undeclared: a read-only tool succeeded (it
    answered); a tool that changes things did NOT — silence is not success."""
    declared = getattr(result, "status", None)
    if declared is not None:
        return declared
    return Status.FAILED if changes_things else Status.SUCCEEDED


@dataclass
class ActionRecord:
    tool: str
    input: dict
    status: Status = Status.UNKNOWN          # until closed: an attempt nobody finished is unknown
    correctable: bool = False                 # a refusal of the request as sent (see refused())
    summary: str = ""                         # first line of what the handler said
    show: str | None = None                   # delivered to the person verbatim (this turn only; not stored)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)


class ActionLog(Protocol):
    """Where a turn's attempts are recorded. `begin` is called BEFORE the
    handler runs and returns a handle — or None when the attempt could NOT be
    put on record, in which case a changing action does not run. `finish`
    closes it. Neither raises."""
    def begin(self, tool: str, input: dict) -> object | None: ...
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


@dataclass(frozen=True)
class Erased:
    """An erase, store by store. `left_in_vault`: pages where the text was edited
    by hand, so it was left on purpose. `uncertain`: stores whose clean-up FAILED
    or can't be established — the thing may still be there."""
    erased: bool
    left_in_vault: tuple[str, ...] = ()
    uncertain: tuple[str, ...] = ()


def _resolves(refusal: ActionRecord, later: ActionRecord) -> bool:
    """Is `later` the refused request, corrected? Same tool, succeeded, and the
    two inputs differ in exactly ONE field whose wording still overlaps — a rule
    reworded shorter, not a different rule. When in doubt: not resolved, and the
    person is told."""
    if later.tool != refusal.tool or later.status is not Status.SUCCEEDED:
        return False
    keys = set(refusal.input) | set(later.input)
    differing = [k for k in keys if refusal.input.get(k) != later.input.get(k)]
    if len(differing) != 1:
        return False
    if refusal.input.get(differing[0]) in (None, "", [], {}):
        return True                       # the refusal was for a missing field; the retry supplied it
    before, after = (str(x.input.get(differing[0], "")) for x in (refusal, later))
    words = lambda s: {w.strip(".,;:!?—-'\"()").lower() for w in s.split()} - {""}   # noqa: E731
    a, b = words(before), words(after)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.25


def receipt(actions: list[ActionRecord] | tuple[ActionRecord, ...]) -> str:
    """What the person must be told whatever the model wrote: every action that
    did not cleanly succeed, from the record. Empty when all went well."""
    lines = []
    for position, action in enumerate(actions):
        if action.status is Status.FAILED:
            # A refusal of the request AS SENT, resolved by that same request
            # corrected, got the person what they asked for — not worth a
            # warning. It stays on the record. Matching the tool is not enough:
            # a different note saving does not make this one saved. A plain
            # failure, and UNKNOWN, are never cleared.
            if action.correctable and any(_resolves(action, later) for later in actions[position + 1:]):
                continue
            lines.append(f"⚠️ Not done: {action.summary or action.tool}")
        elif action.status is Status.PARTIAL:
            lines.append(f"⚠️ Partly done: {action.summary or action.tool}")
        elif action.status is Status.UNKNOWN:
            lines.append(
                f"⚠️ Not confirmed: {action.tool} hit an error part-way. It may or may not "
                "have gone through — I haven't retried it."
            )
    return "\n".join(lines)


class PostgresActionLog:
    """The durable record, bound to one person for one turn. `begin` returns
    None when the attempt could not be written — a changing action then does not
    start, because a crash afterwards would leave a change nobody can account
    for. The turn's entries are also kept in memory, so a turn that dies can say
    what it had done without another read."""

    def __init__(self, database, user_id) -> None:
        self._database = database
        self._user_id = user_id
        self.entries: list[ActionRecord] = []

    def begin(self, tool: str, input: dict) -> ActionRecord:
        record = ActionRecord(tool=tool, input=dict(input))
        try:
            with self._database.connect() as conn, conn.cursor() as cur:
                # The log holds what was asked of each tool — their words. It is
                # there to account for recent turns, not to keep them: 30 days.
                cur.execute(
                    "DELETE FROM action_log WHERE user_id = %s AND started_at < NOW() - INTERVAL '30 days'",
                    (self._user_id,),
                )
                cur.execute(
                    "INSERT INTO action_log (id, user_id, tool, input) VALUES (%s, %s, %s, %s::jsonb)",
                    (record.id, self._user_id, tool, json.dumps(input, default=str)),
                )
        except Exception:
            # Not on record: the caller must not start a changing action.
            _log.warning("action log: the attempt could not be recorded", exc_info=True)
            return None
        self.entries.append(record)
        return record

    def finish(self, handle: ActionRecord, status: Status, summary: str) -> None:
        handle.status, handle.summary = status, summary
        handle.finished_at = datetime.now(timezone.utc)
        try:
            with self._database.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE action_log SET status = %s, summary = %s, finished_at = %s WHERE id = %s",
                    (str(status), summary, handle.finished_at, handle.id),
                )
        except Exception:
            _log.warning("action log: finish not written", exc_info=True)


def describe(actions: list[ActionRecord]) -> str:
    """The record in one line, for conversation history."""
    return "; ".join(
        f"{a.tool} → {a.status.upper()}" + (f": {a.summary}" if a.summary else "") for a in actions)
