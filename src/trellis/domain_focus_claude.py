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
# SENSE_GUIDANCE / MOVE_COACH_GUIDANCE).
FOCUS_GUIDANCE = """\
Organising know-how (when the topic is getting things out of their head and into order — in your own Trellis voice):

- Preserve first. The raw input is captured whole before anything else happens to it; synthesis \
sits alongside — reflect back what matters, cleaned thoughts and surfaced actions, without \
judgment and without padding. Keeping something never commits them to doing it: an idea is not \
an obligation.
- Surface only what's relevant now — never the overwhelming everything-list. You remember the \
full picture so they don't have to see it.
- "What should I do?" gets a fit, not a list. Offer the few things that match their energy, \
time, and state right now — pulled from the full picture they don't have to look at. Low-energy \
days get low-energy wins; the hard things wait their turn but never vanish — resurface them when \
there's capacity. A periodic "what have I got?" tidy-up (cleanup_session) keeps the pile honest.
- Efforts are projects that grow. When they're building one, additions belong on its page — \
don't scatter new homes for things that already have one. Research findings worth keeping land \
there too (save_to_effort); researching a seed graduates it (pass graduated_seed_id), and \
reusing the same effort_title builds the page up over time.
"""

# ---------------------------------------------------------------------------
# Prompts — module-level constants, never inline
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """\
You are the synthesis layer of a second brain. The user has sent a brain dump — \
raw, unfiltered, possibly garbled or typo-ridden text from a Telegram message. \
Your job is to process it without losing anything.

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

Rules:
- cleaned_text: rewrite into coherent prose. Fix typos and garbled language. \
Preserve every idea, including the weird tangential ones — those are often the \
most interesting. Nothing is lost or summarised away.
- capture_type: what is this primarily? Stream of connected thoughts = brain_dump. \
Single clear idea = idea. Explicit to-do = task. Open question = question. \
Link or source = reference.
- summary: one line, max 80 characters. What would you title this in a daily note?
- extracted_tasks: only explicit or strongly implied actions. "I need to call the \
dentist Thursday" → task. "I wonder if tracking systems could replace agriculture" \
→ NOT a task, it's an idea/question.
- kind: "todo" = admin she owes — obligations, errands, things with consequences \
if missed ("confirm Erin is coming", "buy wine"). "seed" = curiosity she might \
feed — explorations, research, things to look into with zero obligation \
("look into ceramics", "research drum machines"). Seeds never get a due date. \
When in doubt: would ignoring it forever cost her anything? No → seed.
- due: resolve relative phrases ("Thursday", "tomorrow at 10") to an explicit \
LOCAL date using the current date you are given: "YYYY-MM-DDTHH:MM" if a time was \
mentioned, "YYYY-MM-DD" if only a day. Null if no deadline was mentioned. \
Never convert timezones — local wall-clock time exactly as the user means it.
- energy: how much mental/physical energy this task likely needs. low = routine, \
high = requires full focus.
- questions: genuine open questions worth holding and returning to.
- effort_hints: topics with depth that might be worth an ongoing Effort. \
Only if there is real substance — not every message needs hints. Empty array is fine.\
"""

_EFFORT_SUGGESTION_SYSTEM = """\
You are reviewing a set of recent brain dumps and captures to find recurring themes \
worth naming as an Effort — an ongoing area of work or exploration the user keeps \
returning to without yet having given it a home.

Return ONLY valid JSON:
{
  "suggestions": [
    {
      "title": "...",
      "rationale": "...",
      "intensity": "active" | "simmering" | "dormant"
    }
  ]
}

Rules:
- Only suggest if a theme appears meaningfully across multiple captures.
- Title should be short and evocative, not clinical.
- Rationale: one sentence on why this feels like an Effort rather than a passing thought.
- Intensity: active = clearly working on this now. simmering = keeps coming up but \
not urgent. dormant = interesting but not active. Default to simmering when unsure.
- Empty suggestions array is fine if nothing stands out.\
"""


# ---------------------------------------------------------------------------
# Synthesis result dataclass
# (returned by suggest_efforts — not in models since it's Claude-layer only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffortSuggestion:
    title: str
    rationale: str
    intensity: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class BrainDumpClaude:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def synthesise(self, raw_text: str, current_date_line: str) -> BrainDumpResult | None:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                system=f"{_SYNTHESIS_SYSTEM}\n\nCurrent date and time: {current_date_line}",
                messages=[{"role": "user", "content": raw_text}],
            )
            raw = response.content[0].text.strip()
            return _parse_synthesis(raw)
        except Exception:
            _log.warning("BrainDumpClaude.synthesise failed", exc_info=True)
            return None

    def suggest_efforts(self, capture_summaries: list[str]) -> list[EffortSuggestion]:
        if not capture_summaries:
            return []
        combined = "\n".join(f"- {s}" for s in capture_summaries)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                system=_EFFORT_SUGGESTION_SYSTEM,
                messages=[{"role": "user", "content": f"Recent captures:\n{combined}"}],
            )
            raw = response.content[0].text.strip()
            return _parse_effort_suggestions(raw)
        except Exception:
            _log.warning("BrainDumpClaude.suggest_efforts failed", exc_info=True)
            return []


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


def _parse_effort_suggestions(raw: str) -> list[EffortSuggestion]:
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        _log.warning("BrainDumpClaude: effort suggestions response was not valid JSON")
        return []

    results = []
    for s in data.get("suggestions", []):
        if not isinstance(s, dict) or not s.get("title"):
            continue
        intensity = s.get("intensity", "simmering")
        if intensity not in ("active", "simmering", "dormant"):
            intensity = "simmering"
        results.append(EffortSuggestion(
            title=str(s["title"]).strip(),
            rationale=str(s.get("rationale", "")).strip(),
            intensity=intensity,
        ))
    return results
