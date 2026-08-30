"""
Tools for the Sense (Mind) room — wellbeing tracking + readiness. The reflecting
happens in the oracle turn (guidance in domain_sense_claude); these let it log how
they're doing and read what's stored.

The room owns ONE tool — log_state (a write). Reads are context, not tools: the
context loader (when Sense is routed) carries recent tracking + readiness, and the
snapshot carries them every turn — so no dispatch-read tool is needed.

Handler signature: (user_id, input_dict, now) -> str
Context loader: sense_context_loader (Tier 1b — guidance + recent tracking w/ IDs + readiness)
Snapshot: sense_snapshot (Tier 2 — today's state + cycle + readiness, existence only)
Registration: sense_tools(...)
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime, time as _time
from typing import Any, Callable
from uuid import UUID

from trellis.domain_sense_claude import SENSE_GUIDANCE
from trellis.domain_sense_models import TrackingEventType

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

LOG_STATE_TOOL: dict = {
    "name": "log_state",
    "description": (
        "Log how the user is doing right now — energy, mood, and body/context events. "
        "Call whenever they describe their state (answering a check-in or spontaneously), "
        "or mention taking meds, sleep, or their period. Multiple logs per day are "
        "expected; the within-day curve is the point. Derive scores from their words; "
        "never ask them to rate themselves."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": (
                    "Their words about how they're doing, first person, verbatim — "
                    "never paraphrase into third person ('feeling flat', not "
                    "'they feel flat'). Voice notes: transcript as they said it."
                ),
            },
            "felt_at": {
                "type": "string",
                "description": (
                    "When this state was FELT, if different from now: user-local "
                    "YYYY-MM-DDTHH:MM. 'This morning I was shit' said at noon → today ~09:00; "
                    "'yesterday I crashed after work' → yesterday ~17:00. Omit for right now. "
                    "If one message describes several states at different times, call this "
                    "tool once per state with its own felt_at."
                ),
            },
            "energy": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "Energy derived from their words: 1 empty/shutdown, 3 okay, 5 on top of the world. Omit if they said nothing about energy.",
            },
            "mood": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "Mood derived from their words: 1 awful, 3 neutral, 5 great. Omit if unclear. Mood and energy are independent — 'good mood, sleepy' is mood 4, energy 2.",
            },
            "meds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "e.g. 'ibuprofen'"},
                        "time": {"type": "string", "description": "Local HH:MM if they said when; omit otherwise."},
                    },
                    "required": ["name"],
                },
                "description": "Medication they mention taking.",
            },
            "sleep_hours": {
                "type": "number",
                "description": "Hours slept last night, if mentioned.",
            },
            "sleep_quality": {
                "type": "string",
                "description": "Their description of sleep quality, if mentioned: 'badly', 'great', etc.",
            },
            "period": {
                "type": "string", "enum": ["started", "ended"],
                "description": "If they say their period started or ended.",
            },
            "period_date": {
                "type": "string",
                "description": (
                    "YYYY-MM-DD — ONLY when the period event was on a PAST date "
                    "(e.g. logging history from another app). Omit for today. "
                    "For several past cycles, call once per event with its date."
                ),
            },
        },
        "required": [],
    },
}

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_log_state(user_id: UUID, input_dict: dict, now: datetime, *, sense_service, tz) -> str:
    note = str(input_dict.get("note", "")).strip()
    energy = input_dict.get("energy")
    mood = input_dict.get("mood")
    parts: list[str] = []

    # A state entry needs their words; pure events (period/meds/sleep) don't —
    # forcing a note here is how phantom state rows got fabricated (3 Aug).
    if note:
        felt_at = None
        felt_str = str(input_dict.get("felt_at", "")).strip()
        if felt_str:
            try:
                felt_at = datetime.fromisoformat(felt_str)
                if felt_at.tzinfo is None:
                    felt_at = felt_at.replace(tzinfo=tz)
            except ValueError:
                felt_at = None

        log = sense_service.log_state(
            user_id, note,
            energy=int(energy) if energy is not None else None,
            mood=int(mood) if mood is not None else None,
            now=now,
            felt_at=felt_at,
        )
        scores = ", ".join(
            s for s in (
                f"energy {log.energy}" if log.energy else "",
                f"mood {log.mood}" if log.mood else "",
            ) if s
        )
        parts.append(f"State logged{f' ({scores})' if scores else ''}.")

    for med in input_dict.get("meds") or []:
        if not isinstance(med, dict) or not med.get("name"):
            continue
        occurred = now
        time_str = str(med.get("time", "")).strip()
        if time_str:
            try:
                h, m = time_str.split(":")
                occurred = now.astimezone(tz).replace(
                    hour=int(h), minute=int(m), second=0, microsecond=0
                )
            except (ValueError, AttributeError):
                pass
        sense_service.log_event(
            user_id, TrackingEventType.MEDS,
            detail=str(med["name"]).strip(), occurred_at=occurred,
        )
        parts.append(f"Meds logged: {med['name']}{f' at {time_str}' if time_str else ''}.")

    sleep_hours = input_dict.get("sleep_hours")
    sleep_quality = input_dict.get("sleep_quality")
    if sleep_hours is not None or sleep_quality:
        sense_service.log_event(
            user_id, TrackingEventType.SLEEP,
            detail=str(sleep_quality).strip() if sleep_quality else None,
            value=float(sleep_hours) if sleep_hours is not None else None,
            occurred_at=now,
        )
        parts.append("Sleep logged.")

    period = input_dict.get("period")
    if period in ("started", "ended"):
        occurred = now
        when = ""
        period_date_str = str(input_dict.get("period_date", "")).strip()
        if period_date_str:
            try:
                d = _date.fromisoformat(period_date_str)
                occurred = datetime.combine(d, _time(10, 0), tzinfo=tz)
                when = f" ({period_date_str})"
            except ValueError:
                return f"period_date '{period_date_str}' isn't a valid YYYY-MM-DD date — nothing was logged."
        sense_service.log_event(
            user_id,
            TrackingEventType.PERIOD_START if period == "started" else TrackingEventType.PERIOD_END,
            occurred_at=occurred,
        )
        parts.append(f"Period {period}{when}.")

    if not parts:
        return "Nothing to log — pass their words (note) and/or meds, sleep, or period."

    summary = sense_service.today_summary(user_id, now)
    if summary:
        parts.append(summary)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_tracking(states: list, events: list) -> "str | None":
    """Recent state logs + events with IDs (IDs let delete_entry erase a specific
    entry). None when there's nothing. Used by the context loader."""
    if not states and not events:
        return None
    lines = []
    if states:
        lines.append("State logs (last 7 days):")
        for s in states:
            scores = "/".join(p for p in (
                f"e{s.energy}" if s.energy else "", f"m{s.mood}" if s.mood else "",
            ) if p)
            felt = s.felt_at.strftime("%d %b %H:%M")
            lines.append(f"  [{s.id}] {felt} {scores or '·'} — {s.note[:80]}")
    if events:
        lines.append("Events:")
        for e in events:
            bits = [str(e.event_type)]
            if e.detail:
                bits.append(e.detail)
            if e.value is not None:
                bits.append(f"{e.value:g}")
            lines.append(f"  [{e.id}] {e.occurred_at.strftime('%d %b %H:%M')} — {' '.join(bits)}")
    return "\n".join(lines)


def _fmt_health(health: "dict | None") -> "str | None":
    """One-line readiness summary from recent_health(), or None if there's nothing.
    Shared by the context loader and the snapshot.

    Staleness is stated loudly, not as a discoverable date: yesterday's numbers
    presented as "your readiness" is exactly the honesty failure Trellis exists
    to avoid. Python computed stale_days; here it becomes an instruction."""
    if not health:
        return None
    bits = []
    stale = health.get("stale_days")
    if stale == 0:
        synced = health.get("synced_at")
        if synced:
            bits.append(
                f"TODAY, synced {synced} — sleep/HRV are fixed at wake, but "
                "body battery and steps move all day, and Garmin only holds "
                "what the watch last uploaded: they're as-of the last watch "
                "sync, never 'now'. If one looks odd or old, say so — "
                "sync_garmin refreshes from Garmin but can't make the watch upload"
            )
        else:
            bits.append("TODAY")
    elif stale is not None:
        ago = "YESTERDAY" if stale == 1 else f"{stale} DAYS AGO"
        bits.append(
            f"STALE — from {ago} ({health.get('date', '?')}), NOT today's. "
            "Today's sleep/HRV haven't synced yet: call sync_garmin before advising "
            "on readiness, or clearly say which day the numbers are from"
        )
    elif health.get("date"):
        bits.append(f"as of {health['date']}")
    if health.get("sleep_score") is not None:
        s = f"sleep {health['sleep_score']}"
        if health.get("sleep_hours") is not None:
            s += f" ({health['sleep_hours']}h)"
        bits.append(s)
    elif health.get("sleep_hours") is not None:
        bits.append(f"slept {health['sleep_hours']}h")
    if health.get("hrv_last_night") is not None:
        h = f"HRV {health['hrv_last_night']}"
        if health.get("hrv_status"):
            h += f" ({health['hrv_status']})"
        bits.append(h)
    if health.get("body_battery_high") is not None:
        # "was … as of" is deliberate: the wire between watch and Garmin can lag
        # invisibly, so the number must never be phrasable as the current level.
        bits.append(
            f"body battery was {health['body_battery_high']} as of last watch sync"
        )
    if health.get("resting_hr") is not None:
        bits.append(f"RHR {health['resting_hr']}")
    if health.get("avg_stress") is not None:
        bits.append(f"stress {health['avg_stress']}")
    return ", ".join(bits) if bits else None


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + snapshot (Tier 2)
# ---------------------------------------------------------------------------

def sense_context_loader(sense_service) -> ContextLoader:
    """Loaded when Sense is routed. Carries the wellbeing guidance, recent tracking
    (state/meds/sleep/period, with IDs so delete_entry can target one), and the
    latest readiness — so the room reflects from what's stored, no read tool needed."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [SENSE_GUIDANCE]
        try:
            tracking = _fmt_tracking(
                sense_service.recent_states(user_id, days=7, now=now),
                sense_service.recent_events(user_id, days=7, now=now),
            )
            if tracking:
                parts.append(tracking)
        except Exception:
            _log.warning("sense_context: tracking failed", exc_info=True)
        try:
            line = _fmt_health(sense_service.recent_health(user_id, now=now))
            if line:
                parts.append("Recent Garmin readiness: " + line)
        except Exception:
            _log.warning("sense_context: readiness failed", exc_info=True)
        return "[Wellbeing]\n" + "\n\n".join(parts)

    return loader


def sense_snapshot(sense_service) -> ContextLoader:
    """Tier 2 — today's state, cycle day, and latest readiness — existence only,
    always loaded."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = []
        try:
            state_line = sense_service.today_summary(user_id, now)
            if state_line:
                parts.append(state_line)
            cycle = sense_service.cycle_day(user_id, now)
            if cycle is not None:
                parts.append(f"Cycle day {cycle}")
        except Exception:
            _log.warning("sense_snapshot: state failed", exc_info=True)
        try:
            health = _fmt_health(sense_service.recent_health(user_id, now=now))
            if health:
                parts.append(f"Readiness: {health}")
        except Exception:
            _log.warning("sense_snapshot: readiness failed", exc_info=True)
        return " | ".join(parts) if parts else None

    return loader


# ---------------------------------------------------------------------------
# Routing signals + self-description
# ---------------------------------------------------------------------------

SENSE_SIGNALS: list[str] = [
    "mood", "energy", "tired", "exhausted", "flat", "how i feel", "feeling",
    "sleep", "slept", "meds", "medication",
    "period", "cycle", "hrv", "readiness", "body battery", "resting heart rate",
    "stress", "recovery", "check-in", "checkin",
]

# The rooms inside the sense house (Mind / monitoring), for semantic routing.
# Each phrase is embedded separately and the house scores by its BEST-matching
# room — keep phrases short, concrete and 2+ words (single words are noise
# magnets; so are time words like "today"/"daily", which drag in scheduling
# and greeting messages; conversational phrasing attracts unrelated chat).
# 'recovery and readiness' + 'readiness score' are deliberately both here:
# the first catches "am I recovered enough?", the second "how's my readiness?".
SENSE_ROOMS: list[str] = [
    "mood and energy",
    "stress and overwhelm",
    "medication and meds",
    "menstrual cycle and period",
    "sleep quality",
    "HRV and resting heart rate",
    "body battery",
    "recovery and readiness",
    "readiness score",
    "wellbeing tracking",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def sense_tools(sense_service, tz) -> list[tuple[dict, Any]]:
    # ONE tool: log_state (a write). Reads are context (loader + snapshot), not a
    # dispatch tool — keeps the app tool count flat (no new tool added by this room).
    return [
        (
            LOG_STATE_TOOL,
            lambda uid, inp, now: handle_log_state(uid, inp, now, sense_service=sense_service, tz=tz),
        ),
    ]
