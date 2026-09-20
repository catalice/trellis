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

from trellis.core_actions import done, refused
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
        "Write how they are to the tracking log: their words plus the facts those "
        "words carry — energy, mood, meds, sleep, period, any tracked kind. Call when "
        "they say how they are, in a check-in or in passing, or mention meds, sleep or "
        "their period. One row per state: several states at different times in one "
        "message = one call each with its own felt_at. A state row needs their words; "
        "meds, sleep and period log without one. A period start begins the cycle-day "
        "count. Result reports today's log — read it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "State: their words, first person, verbatim. Voice: the transcript as said.",
            },
            "felt_at": {
                "type": "string",
                "description": "When this account is ABOUT, if not now — local YYYY-MM-DDTHH:MM. Dates everything in the call: state, meds, sleep. Omit for now.",
            },
            "energy": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "State: 1 empty – 5 on top of the world, from their words. Omit if unsaid.",
            },
            "mood": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "State: 1 awful – 5 great, from their words; independent of energy. Omit if unsaid.",
            },
            "meds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "As they said it."},
                        "time": {"type": "string", "description": "Local HH:MM on that day, if said. Unreadable is refused."},
                    },
                    "required": ["name"],
                },
                "description": "Meds: what they took.",
            },
            "sleep_hours": {
                "type": "number",
                "description": "Sleep: hours last night, if said.",
            },
            "sleep_quality": {
                "type": "string",
                "description": "Sleep: their word for it, if said.",
            },
            "period": {
                "type": "string", "enum": ["started", "ended"],
                "description": "Period: started or ended, if said.",
            },
            "extra": {
                "type": "object",
                "description": (
                    "State: other kinds their words carry, {kind: value} — 1-5 or true. "
                    "Reuse the kind names in your context; a new name is a new kind, snake_case."
                ),
            },
            "period_date": {
                "type": "string",
                "description": "Period: YYYY-MM-DD only when the event was on a past date. Omit for today. One call per past event.",
            },
        },
        "required": [],
    },
}

SENSE_GET_TOOL: dict = {
    "name": "sense_get",
    "description": (
        "Read the tracked history beyond what context carries. what=days: one line "
        "per day over a range — mood, energy, meds, sleep, every tracked kind. "
        "what=cycle: every period start, average length, next expected window — "
        "Python's maths. The last 7 days already ride your context; reach for this "
        "when the question is further back."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["days", "cycle"]},
            "since": {"type": "string", "description": "days: start YYYY-MM-DD. Ranges over 120 days are cut to the last 120."},
            "until": {"type": "string", "description": "days: end YYYY-MM-DD, default today."},
        },
        "required": ["what"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_sense_get(user_id: UUID, input_dict: dict, now: datetime, *, sense_service, tz) -> str:
    what = str(input_dict.get("what", "")).strip()

    if what == "cycle":
        c = sense_service.cycle_summary(user_id)
        if c is None:
            return "Fewer than two period starts logged — no cycle maths yet."
        starts = ", ".join(d.strftime("%-d %b") for d in c["starts"][-8:])
        return (
            f"Cycle: {len(c['starts'])} starts logged (recent: {starts}). "
            f"Length avg {c['avg_days']}d, range {c['min_days']}-{c['max_days']}d. "
            f"Last start {c['last_start'].strftime('%-d %b')}; next expected "
            f"~{c['next_expected'].strftime('%-d %b')} "
            f"(window {c['window_start'].strftime('%-d %b')}–{c['window_end'].strftime('%-d %b')})."
        )

    if what == "days":
        from datetime import date as _date, timedelta
        try:
            since = _date.fromisoformat(str(input_dict.get("since", "")).strip())
        except ValueError:
            return "since is required (YYYY-MM-DD)."
        until_raw = str(input_dict.get("until", "")).strip()
        try:
            until = _date.fromisoformat(until_raw) if until_raw else now.astimezone(tz).date()
        except ValueError:
            return f"until {until_raw!r} isn't a valid YYYY-MM-DD date."
        if (until - since).days > 120:
            since = until - timedelta(days=120)
        rows = sense_service.day_rows(user_id, since=since, until=until, now=now)
        if not rows:
            return f"Nothing tracked between {since} and {until}."
        lines = [f"Days {since} to {until} (the Watcher's view):"]
        for d in sorted(rows):
            row = rows[d]
            bits = []
            for k in sorted(row):
                if k in ("logged", "watch") or (k == "meds" and row.get("meds_names")):
                    continue        # bookkeeping for the verifier; names say more than True
                v = ", ".join(row[k]) if k == "meds_names" else row[k]
                bits.append(f"{'meds' if k == 'meds_names' else k} {round(v, 1) if isinstance(v, float) else v}")
            lines.append(f"  {d.isoformat()}: " + ", ".join(bits))
        return "\n".join(lines)

    return "Unknown what. Use: days, cycle."


def handle_log_state(user_id: UUID, input_dict: dict, now: datetime, *, sense_service, tz) -> str:
    note = str(input_dict.get("note", "")).strip()
    energy = input_dict.get("energy")
    mood = input_dict.get("mood")
    parts: list[str] = []

    # Validate EVERYTHING refusable up front: state/meds/sleep from this same
    # call used to be written before a bad period_date bailed out with
    # "nothing was logged" — a lie that earned duplicate rows on the re-send.
    period = input_dict.get("period")
    period_occurred = now
    period_when = ""
    if period in ("started", "ended"):
        period_date_str = str(input_dict.get("period_date", "")).strip()
        if period_date_str:
            try:
                d = _date.fromisoformat(period_date_str)
                period_occurred = datetime.combine(d, _time(10, 0), tzinfo=tz)
                period_when = f" ({period_date_str})"
            except ValueError:
                return refused(f"period_date '{period_date_str}' isn't a valid "
                               "YYYY-MM-DD date — nothing was logged.")

    # ONE account lands on ONE day. felt_at dates everything in this call — the
    # state, the meds, the sleep. ("Yesterday I felt low, took my meds at nine
    # and slept six hours" used to put the state on yesterday and the rest on
    # today.) Every time is read BEFORE anything is written: a time that can't
    # be read is refused, never quietly turned into "now".
    felt_at = None
    felt_str = str(input_dict.get("felt_at", "")).strip()
    if felt_str:
        try:
            felt_at = datetime.fromisoformat(felt_str)
            if felt_at.tzinfo is None:
                felt_at = felt_at.replace(tzinfo=tz)
        except ValueError:
            return refused(f"felt_at {felt_str!r} isn't a time I can read — use local "
                           "YYYY-MM-DDTHH:MM. Nothing was logged.")
    when = felt_at or now                                  # the moment this account is about
    backdated = when.astimezone(tz).date() != now.astimezone(tz).date()
    on_day = f" on {when.astimezone(tz).strftime('%a %-d %b')}" if backdated else ""

    med_times: list[datetime] = []
    for med in input_dict.get("meds") or []:
        if not isinstance(med, dict) or not med.get("name"):
            continue
        time_str = str(med.get("time", "")).strip()
        taken = when
        if time_str:
            try:
                h, m = time_str.split(":")
                taken = when.astimezone(tz).replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            except (ValueError, AttributeError):
                return refused(f"The time {time_str!r} for {med['name']} isn't one I can read — "
                               "use HH:MM, or leave it out. Nothing was logged.")
        med_times.append(taken)

    # A state entry needs their words; pure events (period/meds/sleep) don't —
    # forcing a note here is how phantom state rows got fabricated (3 Aug).
    if note:
        extra = input_dict.get("extra")
        log = sense_service.log_state(
            user_id, note,
            energy=int(energy) if energy is not None else None,
            mood=int(mood) if mood is not None else None,
            now=now,
            felt_at=felt_at,
            extra=extra if isinstance(extra, dict) else None,
        )
        scores = ", ".join(
            s for s in (
                f"energy {log.energy}" if log.energy else "",
                f"mood {log.mood}" if log.mood else "",
            ) if s
        )
        parts.append(f"State logged{f' ({scores})' if scores else ''}{on_day}.")

    meds = [m for m in (input_dict.get("meds") or []) if isinstance(m, dict) and m.get("name")]
    for med, occurred in zip(meds, med_times):
        time_str = str(med.get("time", "")).strip()
        sense_service.log_event(
            user_id, TrackingEventType.MEDS,
            detail=str(med["name"]).strip(), occurred_at=occurred,
        )
        parts.append(f"Meds logged: {med['name']}{f' at {time_str}' if time_str else ''}{on_day}.")

    sleep_hours = input_dict.get("sleep_hours")
    sleep_quality = input_dict.get("sleep_quality")
    if sleep_hours is not None or sleep_quality:
        sense_service.log_event(
            user_id, TrackingEventType.SLEEP,
            detail=str(sleep_quality).strip() if sleep_quality else None,
            value=float(sleep_hours) if sleep_hours is not None else None,
            occurred_at=when,
        )
        parts.append(f"Sleep logged{on_day}.")

    if period in ("started", "ended"):
        sense_service.log_event(
            user_id,
            TrackingEventType.PERIOD_START if period == "started" else TrackingEventType.PERIOD_END,
            occurred_at=period_occurred,
        )
        parts.append(f"Period {period}{period_when}.")

    if not parts:
        return refused("Nothing to log — pass their words (note) and/or meds, sleep, or period.")

    summary = sense_service.today_summary(user_id, now)
    if summary:
        parts.append(summary)
    return done(" ".join(parts))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_logged(rows: list) -> "str | None":
    """The computed 30-day line: what was logged, on how many days, when last."""
    if not rows:
        return None
    bits = []
    for r in rows:
        ago = "today" if r["days_ago"] == 0 else f"{r['days_ago']}d ago"
        bits.append(f"{r['name']} {r['days']}d, last {r['last'].strftime('%-d %b')} ({ago})")
    return "[Logged, last 30 days — computed from what they REPORTED; a day not logged is unknown] " + "; ".join(bits)


def _fmt_tracking(states: list, events: list, tz) -> "str | None":
    """Recent state logs + events with IDs (IDs let delete_entry erase a specific
    entry). None when there's nothing. Used by the context loader."""
    if not states and not events:
        return None
    lines = []
    if states:
        lines.append("REPORTED by them — state logs (last 7 days); e/m scores are read from their words:")
        for s in states:
            scores = "/".join(p for p in (
                f"e{s.energy}" if s.energy else "", f"m{s.mood}" if s.mood else "",
            ) if p)
            felt = s.felt_at.astimezone(tz).strftime("%d %b %H:%M")
            lines.append(f"  [{s.id}] {felt} {scores or '·'} — {s.note[:80]}")
    if events:
        lines.append("REPORTED by them — events:")
        for e in events:
            bits = [str(e.event_type)]
            if e.detail:
                bits.append(e.detail)
            if e.value is not None:
                bits.append(f"{e.value:g}")
            lines.append(f"  [{e.id}] {e.occurred_at.astimezone(tz).strftime('%d %b %H:%M')} — {' '.join(bits)}")
    return "\n".join(lines)


def _fmt_health(health: "dict | None") -> "str | None":
    """One-line readiness summary from recent_health(), or None if there's nothing.
    Shared by the context loader and the snapshot.

    Staleness is stated loudly, not as a discoverable date: yesterday's numbers
    presented as "your readiness" is exactly the honesty failure Trellis exists
    to avoid. Python computed stale_days; here it becomes a label. What the
    numbers MEAN (day-level aggregates, watch→cloud lag) lives once, in the
    Sense/Move guidance — this line carries the facts and the day, not prose."""
    if not health:
        return None
    bits = []
    stale = health.get("stale_days")
    if stale == 0:
        synced = health.get("synced_at")
        bits.append(f"TODAY, synced {synced}" if synced else "TODAY")
    elif stale is not None:
        ago = "YESTERDAY" if stale == 1 else f"{stale} DAYS AGO"
        bits.append(
            f"STALE — from {ago} ({health.get('date', '?')}), not today's: "
            "sync_garmin, or name the day"
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
    bb_now, bb_high = health.get("body_battery_end"), health.get("body_battery_high")
    if bb_now is not None or bb_high is not None:
        # The LEVEL is the last reading of the day (body_battery_end) — the day's
        # maximum is what they woke with, not where they are. Quoting the max as
        # "your body battery" read 99 on an evening they were at 15 (15 Sep).
        # "as of last watch sync" is deliberate: the wire between watch and
        # Garmin can lag invisibly, so it must never be phrasable as "now".
        level = bb_now if bb_now is not None else bb_high
        peak = f", peaked at {bb_high}" if bb_high is not None and bb_high != level else ""
        bits.append(f"body battery {level} as of last watch sync{peak}")
    if health.get("resting_hr") is not None:
        bits.append(f"RHR {health['resting_hr']}")
    if health.get("avg_stress") is not None:
        bits.append(f"stress {health['avg_stress']}")
    kept = health.get("not_refreshed") or {}
    if kept and bits:
        # "synced HH:MM" is when a sync last RAN. That sync didn't bring these
        # readings, so they are older — said every time, not only in its receipt.
        bits.append("NOT refreshed at the last sync, kept from earlier: " + ", ".join(
            f"{name} (last good {when or 'unknown'})" for name, when in kept.items()))
    return ", ".join(bits) if bits else None


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + snapshot (Tier 2)
# ---------------------------------------------------------------------------

def sense_context_loader(sense_service, tz) -> ContextLoader:
    """Loaded when Sense is routed. Carries the wellbeing guidance, recent tracking
    (state/meds/sleep/period, with IDs so delete_entry can target one), and the
    latest readiness — so the room reflects from what's stored, no read tool needed."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [SENSE_GUIDANCE]
        try:
            c = sense_service.cycle_summary(user_id)
            if c:
                parts.append(
                    f"[Cycle — computed from {len(c['starts'])} logged starts] "
                    f"avg {c['avg_days']}d ({c['min_days']}-{c['max_days']}); "
                    f"last {c['last_start'].strftime('%-d %b')}, next expected "
                    f"~{c['next_expected'].strftime('%-d %b')}"
                )
        except Exception:
            _log.warning("cycle summary failed", exc_info=True)
        try:
            logged = _fmt_logged(sense_service.logged_summary(user_id, days=30, now=now))
            if logged:
                parts.append(logged)
        except Exception:
            _log.warning("logged summary failed", exc_info=True)
        try:
            kinds = sense_service.tracked_kinds(user_id)
            if kinds:
                parts.append("[Tracked kinds]: " + ", ".join(kinds))
        except Exception:
            _log.warning("tracked kinds failed", exc_info=True)
        try:
            tracking = _fmt_tracking(
                sense_service.recent_states(user_id, days=7, now=now),
                sense_service.recent_events(user_id, days=7, now=now),
                tz,
            )
            if tracking:
                parts.append(tracking)
        except Exception:
            _log.warning("sense_context: tracking failed", exc_info=True)
        try:
            line = _fmt_health(sense_service.recent_health(user_id, now=now))
            if line:
                parts.append("OBSERVED by the watch — recent Garmin readiness: " + line)
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
            c = sense_service.cycle_summary(user_id)
            if c:
                parts.append(f"Next period ~{c['next_expected'].strftime('%-d %b')}")
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
    # log_state (the write) + sense_get (the seeing-all door, added 5 Sep 2026:
    # reads-are-context worked at two weeks of data and failed at nine months —
    # months of cycle history were invisible to the fast mind).
    return [
        (
            LOG_STATE_TOOL,
            lambda uid, inp, now: handle_log_state(uid, inp, now, sense_service=sense_service, tz=tz),
        ),
        (
            SENSE_GET_TOOL,
            lambda uid, inp, now: handle_sense_get(uid, inp, now, sense_service=sense_service, tz=tz),
        ),
    ]
