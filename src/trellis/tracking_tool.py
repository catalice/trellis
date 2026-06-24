"""
Tool schemas and handlers for the tracking cross-layer.

These tools go in always_tools (not domain-routed) — they are available
every turn regardless of which domains are active.

Usage in main.py:
    from trellis.tracking_tool import tracking_tools, TRACKING_SIGNALS
    always_tools=[*meta_tools(...), *tracking_tools(health_repository, cycle_service)],
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

_log = logging.getLogger(__name__)


# --- Protocols (structural — no imports from service files) -----------------

class _HealthRepo(Protocol):
    def record_self_report(self, report): ...


class _CycleService(Protocol):
    def record_period_start(self, user_id: UUID, occurred_on: date, *, note: str | None = None): ...
    def record_observation(
        self, user_id: UUID, occurred_on: date, *, note: str | None = None, symptoms: tuple = ()
    ): ...


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LOG_SELF_REPORT_TOOL = {
    "name": "log_self_report",
    "description": (
        "Log a daily self-report: how the user is feeling today across energy, body, "
        "sleep, soreness and life load (all 1–10). Call when she gives scores or "
        "describes how she's feeling in a way that maps to these dimensions. "
        "All scores are optional — only log what they mention."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "energy_score": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "Energy level 1–10.",
            },
            "body_score": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "How the body feels overall 1–10.",
            },
            "sleep_minutes": {
                "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}],
                "description": "Hours slept, expressed as minutes.",
            },
            "soreness_score": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "Muscle soreness 1–10.",
            },
            "life_load_score": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                "description": "Life/mental load 1–10.",
            },
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Optional free-text note.",
            },
        },
        "required": [],
    },
}

LOG_PERIOD_START_TOOL = {
    "name": "log_period_start",
    "description": (
        "Record that the user's period started. Call when they mention her period starting. "
        "Defaults to today if no date is given."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "occurred_on_iso": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Date the period started (YYYY-MM-DD). Defaults to today.",
            },
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Optional note.",
            },
        },
        "required": [],
    },
}

LOG_CYCLE_OBSERVATION_TOOL = {
    "name": "log_cycle_observation",
    "description": (
        "Record a cycle observation — symptoms, spotting, cramps, or general notes. "
        "Call when the user mentions anything cycle-related that isn't a period start."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "occurred_on_iso": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Date of the observation (YYYY-MM-DD). Defaults to today.",
            },
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Free-text note about what was observed.",
            },
            "symptoms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of symptoms, e.g. ['cramps', 'spotting', 'bloating'].",
            },
        },
        "required": [],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _parse_date_iso(value: str | None, fallback: date) -> tuple[date, str | None]:
    if not value:
        return fallback, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return fallback, f"Could not parse date '{value}'. Use YYYY-MM-DD format."


def handle_log_self_report(
    user_id: UUID, input_dict: dict, now: datetime,
    *, health_repository,
) -> str:
    from trellis.health import SelfHealthReport

    today = now.date()
    report = SelfHealthReport(
        user_id=user_id,
        observed_on=today,
        energy_score=input_dict.get("energy_score"),
        body_score=input_dict.get("body_score"),
        sleep_minutes=input_dict.get("sleep_minutes"),
        soreness_score=input_dict.get("soreness_score"),
        life_load_score=input_dict.get("life_load_score"),
        note=input_dict.get("note") or None,
        raw={"source": "trellis_tracking", "tool_input": input_dict},
    )
    health_repository.record_self_report(report)

    parts: list[str] = []
    if report.energy_score is not None:
        parts.append(f"energy {report.energy_score}/10")
    if report.body_score is not None:
        parts.append(f"body {report.body_score}/10")
    if report.life_load_score is not None:
        parts.append(f"life load {report.life_load_score}/10")
    if report.soreness_score is not None:
        parts.append(f"soreness {report.soreness_score}/10")
    if report.sleep_minutes is not None:
        h, m = divmod(report.sleep_minutes, 60)
        parts.append(f"sleep {h}h{m:02d}m")
    if report.note:
        parts.append(f"note: {report.note}")

    if not parts:
        return "Self-report logged (no scores provided)."
    return "Self-report logged: " + ", ".join(parts)


def handle_log_period_start(
    user_id: UUID, input_dict: dict, now: datetime,
    *, cycle_service,
) -> str:
    today = now.date()
    occurred_on, err = _parse_date_iso(input_dict.get("occurred_on_iso"), today)
    if err:
        return err
    cycle_service.record_period_start(
        user_id, occurred_on, note=input_dict.get("note") or None
    )
    return f"Period start recorded for {occurred_on.isoformat()}."


def handle_log_cycle_observation(
    user_id: UUID, input_dict: dict, now: datetime,
    *, cycle_service,
) -> str:
    today = now.date()
    occurred_on, err = _parse_date_iso(input_dict.get("occurred_on_iso"), today)
    if err:
        return err
    symptoms = tuple(input_dict.get("symptoms") or [])
    cycle_service.record_observation(
        user_id,
        occurred_on,
        note=input_dict.get("note") or None,
        symptoms=symptoms,
    )
    parts: list[str] = [f"Cycle observation recorded for {occurred_on.isoformat()}."]
    if symptoms:
        parts.append("Symptoms: " + ", ".join(symptoms))
    if input_dict.get("note"):
        parts.append(f"Note: {input_dict['note']}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

TRACKING_SIGNALS = [
    "cycle", "period", "menstrual", "ovulation", "luteal", "follicular",
    "hrv", "heart rate variability", "sleep score", "body battery", "garmin",
    "self report", "energy score", "mood score", "body score", "soreness",
    "life load", "how am I feeling", "check in", "daily check", "health log",
    "track my", "logged", "log today", "symptom", "cramp", "spotting", "cycle day",
]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def tracking_tools(
    health_repository,
    cycle_service,
) -> list[tuple[dict, callable]]:
    return [
        (LOG_SELF_REPORT_TOOL,
         lambda uid, inp, now: handle_log_self_report(
             uid, inp, now, health_repository=health_repository
         )),
        (LOG_PERIOD_START_TOOL,
         lambda uid, inp, now: handle_log_period_start(
             uid, inp, now, cycle_service=cycle_service
         )),
        (LOG_CYCLE_OBSERVATION_TOOL,
         lambda uid, inp, now: handle_log_cycle_observation(
             uid, inp, now, cycle_service=cycle_service
         )),
    ]
