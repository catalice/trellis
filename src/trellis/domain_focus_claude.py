from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from anthropic import Anthropic

from trellis.domain_focus_models import (
    BrainDumpResult,
    CaptureType,
    ExtractedTask,
    TaskEnergy,
    TaskKind,
    TaskPriority,
)

_log = logging.getLogger(__name__)

# Loaded as Tier-1b guidance whenever the focus house is routed (same pattern as
# SENSE_GUIDANCE / MOVE_COACH_GUIDANCE). Role + what the data means, nothing else —
# tactics listed here became the ceiling (see domain_move_claude).
FOCUS_GUIDANCE = """\
Organiser. Their head, held outside it.
Preserve first: what they gave you is kept whole; what you reflect back sits beside it.
Kept is not committed — an idea is not an obligation.
Show what fits now, never the whole list. You hold the full picture so they don't have to look at it.

Data
Task = todo (owed; costs them if missed) or seed (curiosity; no due date, never nags).
Goal = what they're working toward. A label is only theirs to give.
Effort = a project that grows. Its page is where its things live — one home, not several.
Capture = their words, raw. Inbox = captures not yet homed.
Reminder = their words posted back at a time. Check-in = you, woken at a time with an instruction.
"""

# ---------------------------------------------------------------------------
# Prompts — module-level constants, never inline
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """\
Synthesis layer of a second brain. Input: one raw brain dump from Telegram — \
unfiltered, possibly garbled. Lose nothing.

Return ONLY valid JSON matching this structure exactly:
{
  "capture_type": "brain_dump" | "idea" | "task" | "question" | "reference",
  "cleaned_text": "...",
  "summary": "...",
  "extracted_tasks": [
    {
      "title": "...",
      "kind": "todo" | "seed",
      "energy": "low" | "medium" | "high",
      "priority": "low" | "medium" | "high",
      "due": "2026-07-23T10:00" | "2026-07-23" | null
    }
  ],
  "questions": ["..."],
  "effort_hints": ["..."]
}

- cleaned_text: coherent prose. Fix typos and garbling. Every idea survives, \
tangents included — nothing summarised away.
- capture_type: what it mainly is. Connected thoughts = brain_dump. One clear \
idea = idea. Explicit to-do = task. Open question = question. Link or source = reference.
- summary: one line, max 80 characters — its title in a daily note.
- extracted_tasks: explicit or strongly implied actions only. A musing is not a task.
- kind: todo = owed, costs them if missed. seed = curiosity, zero obligation; \
seeds never get a due date. Would ignoring it forever cost them anything? No → seed.
- due: resolve relative phrases against the current date given, LOCAL wall-clock \
as they mean it: "YYYY-MM-DDTHH:MM" if a time was said, "YYYY-MM-DD" if only a \
day, null if none. Never convert timezones.
- energy: what the task needs. low = routine, high = full focus.
- questions: open questions worth returning to.
- effort_hints: topics with enough depth to be an ongoing Effort. Only with real \
substance; empty is fine.\
"""

class BrainDumpClaude:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def synthesise(
        self, raw_text: str, current_date_line: str, hints: str | None = None,
    ) -> BrainDumpResult | None:
        try:
            system = f"{_SYNTHESIS_SYSTEM}\n\nCurrent date and time: {current_date_line}"
            if hints:
                system += (
                    "\n\nTheir landscape (for routing, never invention): " + hints +
                    "\nWhen a dump clearly belongs to one of these, name it in "
                    "effort_hints instead of inventing a new home."
                )
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": raw_text}],
            )
            # ALL text blocks — adaptive thinking can put a ThinkingBlock
            # first, and content[0].text then crashes (silently killing
            # synthesis: capture saved raw-only, no tasks extracted).
            texts = [
                block.text for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", "")
            ]
            raw = "\n".join(t.strip() for t in texts).strip()
            if not raw:
                _log.warning("BrainDumpClaude: response carried no text blocks")
                return None
            return _parse_synthesis(raw)
        except Exception:
            _log.warning("BrainDumpClaude.synthesise failed", exc_info=True)
            return None

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _strip_json_fences(raw: str) -> str:
    """Claude sometimes wraps JSON in ```json ... ``` despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_synthesis(raw: str) -> BrainDumpResult | None:
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        _log.warning("BrainDumpClaude: synthesis response was not valid JSON")
        return None

    try:
        capture_type = CaptureType(data.get("capture_type", "brain_dump"))
    except ValueError:
        capture_type = CaptureType.BRAIN_DUMP

    tasks = []
    for t in data.get("extracted_tasks", []):
        if not isinstance(t, dict) or not t.get("title"):
            continue
        try:
            kind = TaskKind(t.get("kind", "todo"))
        except ValueError:
            kind = TaskKind.TODO
        try:
            energy = TaskEnergy(t.get("energy", "medium"))
        except ValueError:
            energy = TaskEnergy.MEDIUM
        try:
            priority = TaskPriority(t.get("priority", "medium"))
        except ValueError:
            priority = TaskPriority.MEDIUM
        tasks.append(ExtractedTask(
            title=str(t["title"]).strip(),
            kind=kind,
            energy=energy,
            priority=priority,
            due=t.get("due") or None,
        ))

    cleaned = str(data.get("cleaned_text", "")).strip()
    summary = str(data.get("summary", "")).strip()[:80]

    if not cleaned:
        _log.warning("BrainDumpClaude: synthesis returned empty cleaned_text")
        return None

    return BrainDumpResult(
        cleaned_text=cleaned,
        capture_type=capture_type,
        summary=summary,
        extracted_tasks=tuple(tasks),
        questions=tuple(str(q).strip() for q in data.get("questions", []) if q),
        effort_hints=tuple(str(h).strip() for h in data.get("effort_hints", []) if h),
    )


