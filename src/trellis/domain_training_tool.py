"""
Tools for the running coach — the coach's hands. The coaching itself happens in the
oracle turn (persona in domain_training_claude); these just let it read context and
persist the plan.

Handler signature: (user_id, input_dict, now) -> str
Context loader: training_context_loader (Tier 1b — carries the coach persona)
Snapshot: training_snapshot (Tier 2 — today's run, existence only)
Registration: training_tools(...)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_training_claude import TRAINING_COACH_GUIDANCE

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TRAINING_GET_TOOL: dict = {
    "name": "training_get",
    "description": "Read the running plan (or a run's detail) before telling the user what's on. Use this so you speak from what's actually stored, not memory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["plan", "week", "today", "baseline", "history", "run_detail"],
                "description": (
                    "plan: the arc + the stored week. "
                    "week: this week's REAL dates (weekday->date) plus any stored sessions. "
                    "today: today's stored session. "
                    "baseline: the stored fitness baseline. "
                    "history: recent completed runs — read before reviewing a week or planning the next. "
                    "run_detail: one recent run's per-split/lap breakdown (pace + HR per rep) from "
                    "Garmin, so you can see how the intervals/pacing/HR actually went ('how did my "
                    "intervals go?'). Use the 'which' field to pick which recent run."
                ),
            },
            "which": {
                "type": "integer",
                "description": "For run_detail only: which recent run (0 = most recent, default; 1 = the one before).",
            },
        },
        "required": ["what"],
    },
}

SAVE_TRAINING_PLAN_TOOL: dict = {
    "name": "save_training_plan",
    "description": (
        "Persist the plan you've designed or adjusted. Author it from the REAL dates you "
        "were given (never invent dates). Call whenever you create or change the arc or the "
        "week, so it survives to next time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "description": (
                    'The plan doc: {"arc": "<phases, weeks to goal, where they are now>", '
                    '"week": [{"date": "YYYY-MM-DD", "type": "easy|long|intervals|tempo|recovery|rest", '
                    '"detail": "what to do"}, ...]} using this week\'s real dates.'
                ),
            },
            "baseline": {
                "type": "string",
                "description": "Optional: a short fitness baseline summary to store/update.",
            },
        },
        "required": ["plan"],
    },
}

PUSH_TO_WATCH_TOOL: dict = {
    "name": "push_to_watch",
    "description": (
        "Push a STRUCTURED workout to the user's Garmin watch and schedule it on a real date, "
        "so they just open Garmin and press start (no translating the plan into action). Author "
        "the session as a spec; supports warmup/cooldown, intervals/sprints, tempo, long runs, "
        "recovery, and repeat blocks, with pace or HR targets."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "The real date to schedule it on (YYYY-MM-DD) — use this week's real dates."},
            "workout": {
                "type": "object",
                "description": (
                    'The workout spec: {"name": "6x400m intervals", "steps": [ ... ]}. Each step has '
                    '"kind" (warmup|cooldown|interval|run|recovery|rest|repeat) and EITHER "duration" '
                    '("10min"/"90s"/"45:00") OR "distance" ("400m"/"5km") or neither (open, press lap). '
                    'Optional "note", "pace" ("4:30-4:50" per km), "hr" ("140-150"). A "repeat" step needs '
                    '"times" (int) and nested "steps". Example: {"name":"6x400m","steps":[{"kind":"warmup",'
                    '"duration":"10min"},{"kind":"repeat","times":6,"steps":[{"kind":"interval","distance":'
                    '"400m","pace":"4:20-4:40"},{"kind":"recovery","duration":"90s"}]},{"kind":"cooldown","duration":"10min"}]}'
                ),
            },
        },
        "required": ["date", "workout"],
    },
}

SYNC_GARMIN_TOOL: dict = {
    "name": "sync_garmin",
    "description": (
        "Refresh the user's Garmin data now: pull recent runs into the log and update recent "
        "health/readiness (sleep, HRV, body battery). This also runs automatically once a day — "
        "use it when they want their latest data reflected right away ('sync my Garmin')."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_training_get(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    what = str(input_dict.get("what", ""))

    if what == "today":
        session = training_service.todays_session(user_id, now)
        if session is None:
            return "Nothing stored for today. If there's no plan yet, offer to build one."
        return "Today: " + _fmt_session(session)

    if what == "week":
        real = training_service.current_week(now)
        stored = {s.get("date"): s for s in training_service.week_sessions(user_id)}
        lines = ["This week (real dates):"]
        for day in real:
            marker = " <- today" if day["is_today"] else ""
            s = stored.get(day["date"])
            planned = f"  {_fmt_session(s)}" if s else "  (nothing planned yet)"
            lines.append(f"{day['weekday']} {day['date']}{marker}:{planned[1:] if s else planned}")
        return "\n".join(lines)

    if what == "plan":
        plan = training_service.get_plan(user_id)
        if plan is None or not plan.plan:
            return "No plan stored yet. Understand their goal + starting point, then build one."
        arc = plan.plan.get("arc") or "(no arc summary yet)"
        lines = [f"Arc: {arc}"]
        sessions = training_service.week_sessions(user_id)
        if sessions:
            lines.append("Stored week:")
            lines.extend("  " + _fmt_session(s) for s in sessions)
        return "\n".join(lines)

    if what == "baseline":
        plan = training_service.get_plan(user_id)
        if plan is None or not plan.baseline:
            return "No baseline yet. Ask for their Garmin data or what they can currently run."
        return f"Baseline: {plan.baseline}"

    if what == "history":
        runs = training_service.recent_runs(user_id)
        if not runs:
            return "No runs logged yet — sync_garmin pulls recent runs from their watch."
        lines = ["Recent runs (most recent first):"]
        for r in runs:
            dist = f" — {r.distance_km}km" if r.distance_km is not None else ""
            lines.append(f"  {r.ran_on.isoformat()}{dist}: {r.note}")
        return "\n".join(lines)

    if what == "run_detail":
        which = input_dict.get("which")
        try:
            which = max(0, int(which)) if which is not None else 0
        except (TypeError, ValueError):
            which = 0
        try:
            detail = training_service.review_run(user_id, which=which)
        except RuntimeError as exc:
            return str(exc)
        except Exception:
            _log.warning("run_detail failed", exc_info=True)
            return "Couldn't reach Garmin just now — try again in a moment."
        if detail is None:
            return "No run found to review. Sync Garmin first, or check the number."
        return _fmt_run_detail(detail)

    return "Unknown request. Use what: plan, week, today, baseline, history, or run_detail."


def handle_save_training_plan(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    plan = input_dict.get("plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            return "The plan needs to be a JSON object with 'arc' and 'week'."
    if not isinstance(plan, dict):
        return "The plan needs to be a JSON object with 'arc' and 'week'."
    baseline = input_dict.get("baseline")
    baseline = str(baseline) if baseline is not None else None
    try:
        goals = training_service.training_goals(user_id)
        goal_id = goals[0].id if goals else None
        training_service.save_plan(user_id, plan=plan, baseline=baseline, goal_id=goal_id)
    except Exception:
        _log.warning("save_training_plan failed", exc_info=True)
        return "Couldn't save the plan just now — try again in a moment."
    n = len([s for s in plan.get("week", []) if isinstance(s, dict)])
    return f"Saved the plan ({n} day(s) this week)."


def handle_push_to_watch(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    workout = input_dict.get("workout")
    if isinstance(workout, str):
        try:
            workout = json.loads(workout)
        except json.JSONDecodeError:
            return "The workout needs to be an object with a name and steps."
    if not isinstance(workout, dict):
        return "The workout needs to be an object with a name and steps."
    raw_date = str(input_dict.get("date", "")).strip()
    try:
        on_date = date.fromisoformat(raw_date)
    except ValueError:
        return "I need a real date (YYYY-MM-DD) to schedule it — use one of this week's dates."
    try:
        name = training_service.push_workout_to_watch(user_id, workout, on_date)
    except ValueError as exc:  # WorkoutSpecError
        return f"That workout spec didn't work: {exc}. Check the steps and try again."
    except RuntimeError as exc:
        return str(exc)
    except Exception:
        _log.warning("push_to_watch failed", exc_info=True)
        return "Couldn't push to Garmin just now — try again in a moment."
    return f"Pushed '{name}' to your watch for {on_date.strftime('%a %d %b')}. Open Garmin and press start."


def handle_sync_garmin(user_id: UUID, input_dict: dict, now: datetime, *, training_service) -> str:
    try:
        result = training_service.sync_garmin(user_id, now=now)
    except RuntimeError as exc:
        return str(exc)
    except Exception:
        _log.warning("sync_garmin failed", exc_info=True)
        return "Couldn't reach Garmin just now — try again in a moment."
    bits = []
    new_runs = result.get("new_runs") or 0
    bits.append(f"{new_runs} new run(s)" if new_runs else "no new runs")
    if result.get("health_through"):
        days = result.get("health_records")
        bits.append(f"health up to {result['health_through']}" + (f" ({days} day(s))" if days else ""))
    return "Synced Garmin — " + ", ".join(bits) + "."


def _fmt_run_detail(detail: dict) -> str:
    o = detail["overall"]
    head = o.get("name") or "run"
    bits = []
    if o.get("date"):
        bits.append(o["date"])
    if o.get("distance_km") is not None:
        bits.append(f"{o['distance_km']}km")
    if o.get("duration_min") is not None:
        bits.append(f"{o['duration_min']}min")
    if o.get("avg_hr"):
        bits.append(f"avg HR {o['avg_hr']}")
    if o.get("max_hr"):
        bits.append(f"max HR {o['max_hr']}")
    lines = [f"{head} — " + ", ".join(bits) if bits else head]
    splits = detail.get("splits") or []
    if splits:
        lines.append("Splits:")
        for s in splits:
            seg = [f"  #{s['i']}"]
            if s.get("distance_km") is not None:
                seg.append(f"{s['distance_km']}km")
            if s.get("time"):
                seg.append(s["time"])
            if s.get("pace"):
                seg.append(s["pace"])
            if s.get("avg_hr"):
                seg.append(f"HR {s['avg_hr']}")
            if s.get("max_hr"):
                seg.append(f"(max {s['max_hr']})")
            lines.append(" ".join(seg))
    else:
        lines.append("(no per-split detail available for this run)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + snapshot (Tier 2)
# ---------------------------------------------------------------------------

def training_context_loader(training_service, goal_reader) -> ContextLoader:
    """Loaded only when training is routed. Carries the coach persona + the goal +
    the stored plan + THIS WEEK's real dates, so the coach speaks from reality and
    never invents dates."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [TRAINING_COACH_GUIDANCE]

        try:
            goals = goal_reader.list_training_goals(user_id)
            if goals:
                parts.append("Training goal(s):\n" + "\n".join(f"  {g.summary()}" for g in goals))
            else:
                parts.append("No training goal set yet — help them define a realistic one.")
        except Exception:
            _log.warning("training_context: goals load failed", exc_info=True)

        try:
            plan = training_service.get_plan(user_id)
            if plan is None or not plan.plan:
                parts.append("No plan stored yet — understand their starting point, then build one.")
            else:
                if plan.plan.get("arc"):
                    parts.append("Arc: " + str(plan.plan["arc"]))
                if plan.baseline:
                    parts.append("Baseline: " + plan.baseline)
        except Exception:
            _log.warning("training_context: plan load failed", exc_info=True)

        # Recent health/readiness (if Garmin health is synced) — so the coach can
        # factor sleep/HRV/body battery into the week. Best-effort, skipped if none.
        try:
            health = training_service.recent_health(user_id)
            if health:
                bits = []
                if health.get("date"):
                    bits.append(f"as of {health['date']}")
                if health.get("sleep_score") is not None:
                    bits.append(f"sleep score {health['sleep_score']}")
                if health.get("sleep_hours") is not None:
                    bits.append(f"{health['sleep_hours']}h sleep")
                if health.get("hrv_last_night") is not None:
                    hrv = f"HRV {health['hrv_last_night']}"
                    if health.get("hrv_status"):
                        hrv += f" ({health['hrv_status']})"
                    bits.append(hrv)
                if health.get("body_battery_high") is not None:
                    bits.append(f"body battery {health['body_battery_high']}")
                if health.get("resting_hr") is not None:
                    bits.append(f"resting HR {health['resting_hr']}")
                if health.get("avg_stress") is not None:
                    bits.append(f"stress {health['avg_stress']}")
                if bits:
                    parts.append("Recent health/readiness: " + ", ".join(bits))
        except Exception:
            _log.warning("training_context: health load failed", exc_info=True)

        # Always give the real calendar so runs land on real days.
        try:
            week = training_service.current_week(now)
            parts.append(
                "This week's real dates:\n"
                + "\n".join(f"  {d['weekday']} {d['date']}" + (" (today)" if d["is_today"] else "") for d in week)
            )
        except Exception:
            _log.warning("training_context: week dates failed", exc_info=True)

        return "[Training]\n" + "\n\n".join(parts)

    return loader


def training_snapshot(training_service) -> ContextLoader:
    """Tier 2 — today's run, existence only, always loaded."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        try:
            session = training_service.todays_session(user_id, now)
        except Exception:
            _log.warning("training_snapshot failed", exc_info=True)
            return None
        if not session:
            return None
        return f"Today's run: {session.get('type', 'run')} — {str(session.get('detail', ''))[:50]}"

    return loader


# ---------------------------------------------------------------------------
# Routing signals
# ---------------------------------------------------------------------------

TRAINING_SIGNALS: list[str] = [
    "run", "running", "ran", "jog", "training", "train",
    "workout", "session", "long run", "easy run", "intervals", "tempo",
    "pace", "mileage", "marathon", "5k", "10k", "half marathon", "race", "coach",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def training_tools(training_service) -> list[tuple[dict, Any]]:
    return [
        (TRAINING_GET_TOOL,
         lambda uid, inp, now: handle_training_get(uid, inp, now, training_service=training_service)),
        (SAVE_TRAINING_PLAN_TOOL,
         lambda uid, inp, now: handle_save_training_plan(uid, inp, now, training_service=training_service)),
        (PUSH_TO_WATCH_TOOL,
         lambda uid, inp, now: handle_push_to_watch(uid, inp, now, training_service=training_service)),
        (SYNC_GARMIN_TOOL,
         lambda uid, inp, now: handle_sync_garmin(uid, inp, now, training_service=training_service)),
    ]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_session(s: dict) -> str:
    stype = s.get("type", "run")
    detail = s.get("detail", "")
    day = s.get("date", "")
    line = f"{day} — {stype}"
    if detail:
        line += f": {detail}"
    return line
