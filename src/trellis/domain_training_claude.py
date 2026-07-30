"""
Training Claude layer — the ONLY place the running arc is designed. Periodisation
is judgment, so it lives here (never hardcoded in Python). Prompts are
module-level constants; the call returns a typed GeneratedPlan or None on failure.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from anthropic import Anthropic

from trellis.domain_training_models import SessionType

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt — module-level constant
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """\
You are the coaching brain of a running training module. Design a concrete, dated \
block of running that moves the user toward their goal — the kind of plan a good \
coach writes so the runner never has to decide "what run is today".

You are given: the user's training goal(s), today's date, and how many days a week \
they want to run. Schedule the NEXT 14 DAYS as concrete dated sessions, starting \
from today.

Return ONLY valid JSON matching this structure exactly:
{
  "rationale": "one sentence on the shape of this block and why",
  "sessions": [
    {
      "date": "YYYY-MM-DD",
      "type": "easy" | "long" | "intervals" | "tempo" | "recovery" | "rest",
      "description": "what to actually do, in plain language",
      "distance_km": 5.0,        // optional, null if not distance-based
      "duration_min": 40         // optional, null if not time-based
    }
  ]
}

Coaching rules:
- Respect the requested runs-per-week; fill the other days with "rest" (a real, \
deliberate rest day is part of the plan, not a gap).
- Build sensibly toward the goal: mostly easy aerobic running, one long run a week, \
and quality (intervals/tempo) only in proportion to the goal and time available. \
Don't pile on hard sessions.
- One long run per week; never back-to-back hard days.
- Be specific and doable. "Easy 5k, conversational pace" beats "go for a run".
- Every day in the next 14 days gets exactly one session (a run or a rest).
- This is a starting structure that will adapt later — make it solid, not fragile.\
"""


# ---------------------------------------------------------------------------
# Result dataclasses (Claude-layer only — not stored as-is)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratedSession:
    scheduled_date: date
    session_type: SessionType
    description: str
    distance_km: float | None
    duration_min: int | None


@dataclass(frozen=True)
class GeneratedPlan:
    rationale: str
    sessions: tuple[GeneratedSession, ...]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TrainingClaude:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def generate_plan(
        self, *, goals_summary: str, today_line: str, days_per_week: int
    ) -> GeneratedPlan | None:
        user = (
            f"Training goal(s):\n{goals_summary}\n\n"
            f"Today: {today_line}\n"
            f"Runs per week wanted: {days_per_week}"
        )
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=_PLAN_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            raw = response.content[0].text.strip()
            return _parse_plan(raw)
        except Exception:
            _log.warning("TrainingClaude.generate_plan failed", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_plan(raw: str) -> GeneratedPlan | None:
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        _log.warning("TrainingClaude: plan response was not valid JSON")
        return None

    sessions: list[GeneratedSession] = []
    for s in data.get("sessions", []):
        if not isinstance(s, dict) or not s.get("date"):
            continue
        try:
            when = date.fromisoformat(str(s["date"]).strip())
        except ValueError:
            continue
        try:
            stype = SessionType(str(s.get("type", "easy")).strip().lower())
        except ValueError:
            stype = SessionType.EASY
        sessions.append(GeneratedSession(
            scheduled_date=when,
            session_type=stype,
            description=str(s.get("description", "")).strip(),
            distance_km=_as_float(s.get("distance_km")),
            duration_min=_as_int(s.get("duration_min")),
        ))

    if not sessions:
        _log.warning("TrainingClaude: plan had no usable sessions")
        return None

    return GeneratedPlan(
        rationale=str(data.get("rationale", "")).strip(),
        sessions=tuple(sessions),
    )


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
