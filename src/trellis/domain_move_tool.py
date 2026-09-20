"""
Tools for the running coach — the coach's hands. The coaching itself happens in the
oracle turn (role in domain_move_claude); these let it read context and persist
the plan. Each description carries what the tool does, when to reach for it, and
the one behaviour that would surprise you — and that fact lives HERE only, never
repeated in the guidance.

Handler signature: (user_id, input_dict, now) -> str
Context loader: move_context_loader (Tier 1b — carries the coach persona)
Snapshot: move_snapshot (Tier 2 — today's run, existence only)
Registration: move_tools(...)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from trellis.core_actions import done, failed, partial, refused, unknown
from trellis.domain_move_claude import MOVE_COACH_GUIDANCE
from trellis.domain_move_service import AmbiguousWorkout, NoSuchWorkout

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

MOVE_GET_TOOL: dict = {
    "name": "move_get",
    "description": "Read what's stored before saying what's on. Speak from the read, not memory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["plan", "week", "today", "baseline", "history", "run_detail", "watch"],
                "description": (
                    "plan: arc + stored week. "
                    "week: this week's real dates + what's stored on each. "
                    "today: today's stored session. "
                    "baseline: stored fitness baseline. "
                    "history: recent workouts, every sport. Overall averages only — the walks are blended in. "
                    "run_detail: one workout from Garmin, any type. Runs: running portion (walks excluded) + laps with pace/HR. The review read. "
                    "watch: what's actually in their Garmin workout library. Check before claiming what's on it. "
                    "(Readiness is in context every turn — not here.)"
                ),
            },
            "which": {
                "type": "integer",
                "description": "run_detail only: 0 = most recent (default), 1 = the one before.",
            },
        },
        "required": ["what"],
    },
}

MOVE_UPDATE_TOOL: dict = {
    "name": "move_update",
    "description": (
        "Write to the training record. what=plan: store the plan — saves MERGE by date "
        "(days sent replace same-dated days, days not sent survive; nothing is removed unless "
        "replace_week=true). what=baseline: wholesale replace. what=workout: their words on a "
        "recorded workout, any sport — how it felt, what the watch can't see; appends, never erases. "
        "The activity is never guessed: a day with several needs sport, and one not synced yet is refused. "
        "Result reports what's now stored — read it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {
                "type": "string",
                "enum": ["plan", "baseline", "workout"],
            },
            "plan": {
                "type": "object",
                "description": (
                    'plan: {"arc": "<where they are, where this is going>", '
                    '"week": [{"date": "YYYY-MM-DD", '
                    '"type": "easy|long|intervals|tempo|recovery|strength|rest", '
                    '"detail": "the session"}, ...]}. Real dates from move_get week. '
                    'Strength days are "strength", never "rest".'
                ),
            },
            "replace_week": {
                "type": "boolean",
                "default": False,
                "description": "plan: True = the sent week IS the week; unsent days are dropped. Full re-author only.",
            },
            "baseline": {
                "type": "string",
                "description": "baseline: the fitness baseline. Result echoes what it overwrote.",
            },
            "date": {
                "type": "string",
                "description": "workout: YYYY-MM-DD (move_get history if unsure).",
            },
            "note": {
                "type": "string",
                "description": "workout: short, in their spirit: 'social run', 'cut short — knee'.",
            },
            "sport": {
                "type": "string",
                "description": "workout: which activity that day — 'run', 'strength', … Needed when the day has more than one; never guessed.",
            },
            "remove": {
                "type": "string",
                "description": "workout: a fragment of their earlier words to take OFF this activity (filed on the wrong one).",
            },
        },
        "required": ["what"],
    },
}

PUSH_TO_WATCH_TOOL: dict = {
    "name": "push_to_watch",
    "description": (
        "Put a structured workout on their Garmin watch for a date. Their watch: agree first. "
        "Pushing again for the same date and name replaces that day's workout; other days are never touched."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, from this week's real dates."},
            "workout": {
                "type": "object",
                "description": (
                    '{"name": "6x400m intervals", "steps": [ ... ]}. Step: "kind" '
                    '(warmup|cooldown|interval|run|recovery|rest|repeat) + "duration" ("10min"/"90s"/"45:00") '
                    'OR "distance" ("400m"/"5km") or neither (open, press lap). Optional "note", '
                    '"pace" ("4:30-4:50" per km), "hr" ("140-150"). "repeat" takes "times" + nested "steps". '
                    'Example: {"name":"6x400m","steps":[{"kind":"warmup","duration":"10min"},'
                    '{"kind":"repeat","times":6,"steps":[{"kind":"interval","distance":"400m","pace":"4:20-4:40"},'
                    '{"kind":"recovery","duration":"90s"}]},{"kind":"cooldown","duration":"10min"}]}'
                ),
            },
        },
        "required": ["date", "workout"],
    },
}

SYNC_GARMIN_TOOL: dict = {
    "name": "sync_garmin",
    "description": (
        "Pull Garmin now: activities + health. Runs daily by itself; call it when they want the latest, or before quoting a number marked stale. "
        "Pulls Garmin's cloud — it can't make the watch upload. Fresh readiness rides back on the receipt."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_move_get(user_id: UUID, input_dict: dict, now: datetime, *, move_service) -> str:
    what = str(input_dict.get("what", ""))

    if what == "today":
        session = move_service.todays_session(user_id, now)
        if session is None:
            return "Nothing stored for today. If there's no plan yet, offer to build one."
        return "Today: " + _fmt_session(session)

    if what == "week":
        real = move_service.current_week(now)
        stored = {s.get("date"): s for s in move_service.week_sessions(user_id)}
        lines = ["This week (real dates):"]
        for day in real:
            marker = " <- today" if day["is_today"] else ""
            s = stored.get(day["date"])
            planned = f"  {_fmt_session(s)}" if s else "  (nothing planned yet)"
            lines.append(f"{day['weekday']} {day['date']}{marker}:{planned[1:] if s else planned}")
        return "\n".join(lines)

    if what == "plan":
        plan = move_service.get_plan(user_id)
        if plan is None or not plan.plan:
            return "No plan stored yet. Understand their goal + starting point, then build one."
        arc = plan.plan.get("arc") or "(no arc summary yet)"
        lines = [f"Arc: {arc}"]
        sessions = move_service.week_sessions(user_id)
        if sessions:
            lines.append("Stored week:")
            lines.extend("  " + _fmt_session(s) for s in sessions)
        return "\n".join(lines)

    if what == "baseline":
        plan = move_service.get_plan(user_id)
        if plan is None or not plan.baseline:
            return "No baseline yet. Ask for their Garmin data or what they can currently run."
        return f"Baseline: {plan.baseline}"

    if what == "history":
        workouts = move_service.recent_workouts(user_id)
        if not workouts:
            return "Nothing recorded yet — sync_garmin pulls recent activities from their watch."
        lines = ["Recent workouts, every sport (most recent first):"]
        for w in workouts:
            kind = (w.activity_type or "").replace("_", " ")
            kind_bit = f" [{kind}]" if kind and "run" not in kind else ""
            dist = f" — {w.distance_km}km" if w.distance_km is not None else ""
            lines.append(f"  {w.ran_on.isoformat()}{kind_bit}{dist}: {w.note}")
        return "\n".join(lines)

    if what == "watch":
        try:
            workouts = move_service.watch_workouts(user_id)
        except RuntimeError as exc:
            return str(exc)
        except Exception:
            _log.warning("watch list failed", exc_info=True)
            return failed("Couldn't reach Garmin just now — nothing was read.")
        if not workouts:
            return "No workouts in their Garmin library."
        lines = ["On their Garmin (workout library, newest first):"]
        lines.extend(f"  {w['name']}" for w in workouts)
        return "\n".join(lines)

    if what == "run_detail":
        which = input_dict.get("which")
        try:
            which = max(0, int(which)) if which is not None else 0
        except (TypeError, ValueError):
            which = 0
        try:
            detail = move_service.review_run(user_id, which=which)
        except RuntimeError as exc:
            return str(exc)
        except Exception:
            _log.warning("run_detail failed", exc_info=True)
            return failed("Couldn't reach Garmin just now — nothing was read.")
        if detail is None:
            return "No recent workout found to review. Sync Garmin first, or check the number."
        return _fmt_run_detail(detail)

    return "Unknown request. Use what: plan, week, today, baseline, history, run_detail, or watch."


def handle_move_update(user_id: UUID, input_dict: dict, now: datetime, *, move_service) -> str:
    """One write door for the training record (the user's call, 15 Sep 2026 — the fold
    that took Move from five tools to four). The proven handlers stay behind it."""
    what = str(input_dict.get("what", "")).strip().lower()
    if what == "plan":
        return _update_plan(user_id, input_dict, move_service=move_service)
    if what == "baseline":
        if not str(input_dict.get("baseline", "")).strip():
            return refused("baseline is required — the fitness baseline text.")
        return _update_plan(user_id, {"plan": {}, "baseline": input_dict["baseline"]},
                            move_service=move_service)
    if what == "workout":
        return _update_workout(user_id, input_dict, move_service=move_service)
    return refused("Unknown request. Use what: plan, baseline, or workout.")


def _update_plan(user_id: UUID, input_dict: dict, *, move_service) -> str:
    plan = input_dict.get("plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            return refused("The plan needs to be a JSON object with 'arc' and 'week'.")
    if not isinstance(plan, dict):
        return refused("The plan needs to be a JSON object with 'arc' and 'week'.")
    baseline = input_dict.get("baseline")
    baseline = str(baseline) if baseline is not None else None
    replace_week = bool(input_dict.get("replace_week", False))
    # Baseline is a wholesale text replace — echo what it overwrote so a bad
    # rewrite is visible in the result, not silently gone.
    old_baseline = None
    if baseline is not None:
        try:
            prev = move_service.get_plan(user_id)
            old_baseline = prev.baseline if prev else None
        except Exception:
            old_baseline = None
    try:
        goals = move_service.training_goals(user_id)
        goal_id = goals[0].id if goals else None
        saved = move_service.save_plan(user_id, plan=plan, baseline=baseline,
                                       goal_id=goal_id, replace_week=replace_week)
    except Exception:
        _log.warning("move_update plan failed", exc_info=True)
        return unknown("Saving the plan hit an error part-way — it may or may not have saved. Read the stored plan before saying which.")
    week = [s for s in saved.plan.get("week", []) if isinstance(s, dict) and s.get("date")]
    sent = len([s for s in plan.get("week", []) if isinstance(s, dict)])
    span = f" ({week[0]['date']} to {week[-1]['date']})" if week else ""
    if not plan and baseline is not None:
        result = "Baseline stored."
    else:
        mode = "Replaced the stored week" if replace_week else f"Merged {sent} day(s) in"
        result = f"{mode}. Stored week now holds {len(week)} session(s){span}."
    if baseline is not None and old_baseline and old_baseline != baseline:
        result += f'\nBaseline replaced — the old one said: "{old_baseline}"'
    return done(result)


def handle_push_to_watch(user_id: UUID, input_dict: dict, now: datetime, *, move_service) -> str:
    workout = input_dict.get("workout")
    if isinstance(workout, str):
        try:
            workout = json.loads(workout)
        except json.JSONDecodeError:
            return refused("The workout needs to be an object with a name and steps.")
    if not isinstance(workout, dict):
        return refused("The workout needs to be an object with a name and steps.")
    raw_date = str(input_dict.get("date", "")).strip()
    try:
        on_date = date.fromisoformat(raw_date)
    except ValueError:
        return refused("I need a real date (YYYY-MM-DD) to schedule it — use one of this week's dates.")
    try:
        pushed = move_service.push_workout_to_watch(user_id, workout, on_date)
    except ValueError as exc:  # WorkoutSpecError
        return refused(f"That workout spec didn't work: {exc}. Check the steps and send it again.")
    except RuntimeError as exc:
        return failed(str(exc))
    except Exception:
        _log.warning("push_to_watch failed", exc_info=True)
        return unknown("The push to Garmin hit an error part-way — the workout may or may not be on the watch. Read the watch library before saying which; do not push again blind.")
    day = on_date.strftime('%a %d %b')
    if pushed.old_copy_left:
        return partial(f"Pushed '{pushed.name}' to the watch for {day} — but the earlier copy for that "
                       "day couldn't be removed, so that day shows two. Delete the older one in Garmin.")
    verb = "Replaced" if pushed.replaced else "Pushed"
    return done(f"{verb} '{pushed.name}' on your watch for {day}. Open Garmin and press start.")


def _update_workout(user_id: UUID, input_dict: dict, *, move_service) -> str:
    raw_date = str(input_dict.get("date", "")).strip()
    note = str(input_dict.get("note", "")).strip()
    sport = str(input_dict.get("sport", "")).strip() or None
    remove = str(input_dict.get("remove", "")).strip() or None
    if not note and not remove:
        return refused("note is required — their account of the workout.")
    try:
        on_date = date.fromisoformat(raw_date)
    except ValueError:
        return refused(f"Invalid date {raw_date!r} — use YYYY-MM-DD (check move_get history).")
    try:
        workout = move_service.annotate_workout(user_id, on_date, note, sport=sport, remove=remove)
    except AmbiguousWorkout as exc:
        return refused(f"Not saved — {raw_date} has several activities: "
                      + "; ".join(_describe_activity(w) for w in exc.candidates)
                      + ". Send it again with sport set to the one they mean.")
    except NoSuchWorkout as exc:
        return failed(f"Not saved — no {exc.sport} activity is recorded on {raw_date} "
                      "(that day has: " + "; ".join(_describe_activity(w) for w in exc.that_day)
                      + "). It may not have synced yet: sync_garmin, then send it again. "
                      "Their words are NOT stored yet — say so.")
    except Exception:
        _log.warning("move_update workout failed", exc_info=True)
        return unknown("Updating that workout hit an error part-way — it may or may not have saved. Read it back before saying which.")
    if workout is None:
        return failed(f"Not saved — nothing is recorded on {raw_date}. It may not have synced yet "
                      "(sync_garmin), or the date is off (move_get history). Their words are NOT stored yet.")
    return done(f"Workout on {raw_date} ({workout.activity_type or 'activity'}) now reads: {workout.note}")


def _describe_activity(workout) -> str:
    return f"{workout.name or workout.note.split(' — ')[0]} ({workout.activity_type or 'unknown sport'})"


def handle_sync_garmin(
    user_id: UUID, input_dict: dict, now: datetime, *, move_service, sense_service=None,
) -> str:
    try:
        result = move_service.sync_garmin(user_id, now=now)
    except RuntimeError as exc:
        return failed(str(exc))
    except Exception:
        _log.warning("sync_garmin failed", exc_info=True)
        return failed("Couldn't reach Garmin just now — nothing was synced; stored readings are unchanged.")
    bits = []
    acts = result.get("activities")
    if acts is not None:
        bits.append(f"{acts} activit{'y' if acts == 1 else 'ies'} refreshed")
    if result.get("health_through"):
        days = result.get("health_records")
        bits.append(f"health up to {result['health_through']}" + (f" ({days} day(s))" if days else ""))
    missed = result.get("unavailable") or {}
    out = ("Partly synced Garmin — " if missed else "Synced Garmin — ") + (", ".join(bits) if bits else "done") + "."
    if missed:
        # Partial is said as partial: the readings below may be older than this sync.
        out += "\nNot refreshed (Garmin request failed; stored readings kept): " + "; ".join(
            f"{day}: {', '.join(groups)}" for day, groups in sorted(missed.items()))
    # The fresh numbers ride back on the receipt: a sync that reports "done"
    # without them left the model quoting the pre-sync figure (15 Sep, body
    # battery 17 vs the 71 that had just landed). Sense owns the data; Move
    # borrows it — same _fmt_health as the context line, so one truth path.
    if sense_service is not None:
        try:
            from trellis.domain_sense_tool import _fmt_health
            line = _fmt_health(sense_service.recent_health(user_id, now=now))
            if line:
                out += f"\nReadiness now: {line}"
        except Exception:
            _log.warning("sync_garmin: readiness readout failed", exc_info=True)
    return partial(out) if missed else done(out)



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
    running = detail.get("running")
    if running:
        r = [f"Running portion (warm-up/cool-down excluded): {running['time']}"]
        if running.get("distance_km") is not None:
            r.append(f"{running['distance_km']}km")
        if running.get("avg_hr"):
            r.append(f"avg HR {running['avg_hr']}")
        lines.append(", ".join(r) + " — judge the run on this, not the overall average")
    if detail.get("not_fetched"):
        lines.append("Not fetched from Garmin at the last sync (request failed): "
                     + ", ".join(detail["not_fetched"]) + " — what's below may be incomplete.")
    splits = detail.get("splits") or []
    if splits:
        aggregated = any(s.get("count") for s in splits)
        lines.append(
            "Split totals by TYPE (no lap-by-lap data for this activity):"
            if aggregated else "Splits (in order):"
        )
        for s in splits:
            if aggregated:
                seg = [f"  {s.get('type', 'segment')} ×{s.get('count', 1)}"]
                if s.get("distance_km") is not None:
                    seg.append(f"{s['distance_km']}km total")
                if s.get("time"):
                    seg.append(f"{s['time']} total")
            else:
                head = f"  #{s['i']}"
                if s.get("type"):
                    head += f" {s['type']}"
                seg = [head]
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

def move_context_loader(move_service, goal_reader) -> ContextLoader:
    """Loaded only when training is routed. Carries the role + the goal + the
    stored plan + THIS WEEK's real dates, so the coach speaks from reality and
    never invents dates."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [MOVE_COACH_GUIDANCE]

        try:
            goals = goal_reader.list_training_goals(user_id)
            if goals:
                parts.append("Training goal(s):\n" + "\n".join(f"  {g.summary()}" for g in goals))
            else:
                parts.append("No training goal set yet — help them define a realistic one.")
        except Exception:
            _log.warning("training_context: goals load failed", exc_info=True)

        try:
            plan = move_service.get_plan(user_id)
            if plan is None or not plan.plan:
                parts.append("No plan stored yet — understand their starting point, then build one.")
            else:
                if plan.plan.get("arc"):
                    parts.append("Arc: " + str(plan.plan["arc"]))
                if plan.baseline:
                    parts.append("Baseline: " + plan.baseline)
        except Exception:
            _log.warning("training_context: plan load failed", exc_info=True)

        # Readiness/recovery (sleep, HRV, body battery) lives in the Sense room and
        # is surfaced every turn by sense_snapshot; the coach reads it from context
        # and factors it into how hard to push.

        # Always give the real calendar so runs land on real days — and use it to
        # catch a stored week left entirely in the past (weekly review skipped):
        # surface that loudly so the coach reviews + re-authors before anything else.
        try:
            week = move_service.current_week(now)
            parts.append(
                "This week's real dates:\n"
                + "\n".join(f"  {d['weekday']} {d['date']}" + (" (today)" if d["is_today"] else "") for d in week)
            )
            monday = week[0]["date"]
            stored = [str(s.get("date", "")) for s in move_service.week_sessions(user_id) if s.get("date")]
            if stored and all(d < monday for d in stored):
                parts.append(
                    "THE STORED WEEK HAS PASSED (last planned day " + max(stored)
                    + "). Review what was run, then author this week — before anything else."
                )
        except Exception:
            _log.warning("training_context: week dates failed", exc_info=True)

        return "[Training]\n" + "\n\n".join(parts)

    return loader


def move_snapshot(move_service) -> ContextLoader:
    """Tier 2 — always loaded every turn (not gated by routing). Surfaces today's
    run (if planned) so the coach always knows it exists. Readiness is in the Sense
    room's snapshot, not duplicated here."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        try:
            session = move_service.todays_session(user_id, now)
        except Exception:
            _log.warning("move_snapshot session failed", exc_info=True)
            return None
        if not session:
            return None
        return f"Today's run: {session.get('type', 'run')} — {str(session.get('detail', ''))[:50]}"

    return loader


# ---------------------------------------------------------------------------
# Routing signals
# ---------------------------------------------------------------------------

MOVE_SIGNALS: list[str] = [
    "run", "running", "ran", "jog", "training", "train",
    "workout", "session", "long run", "easy run", "intervals", "tempo",
    "pace", "mileage", "marathon", "5k", "10k", "half marathon", "race", "coach",
]

# The rooms inside the move house (running coach), for semantic routing. Each
# phrase is embedded separately and the house scores by its BEST-matching room.
# Recovery/readiness rooms belong to the sense house, deliberately absent here.
MOVE_ROOMS: list[str] = [
    "running and training",
    "workout plans and sessions",
    "intervals and long runs",
    "tempo and pace",
    "weekly mileage",
    "races and race goals",
    "building fitness",
    "mobility and stretching",
    "warm-ups and activation",
    "pushing workouts to the Garmin watch",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def move_tools(move_service, sense_service=None) -> list[tuple[dict, Any]]:
    return [
        (MOVE_GET_TOOL,
         lambda uid, inp, now: handle_move_get(uid, inp, now, move_service=move_service)),
        (MOVE_UPDATE_TOOL,
         lambda uid, inp, now: handle_move_update(uid, inp, now, move_service=move_service)),
        (PUSH_TO_WATCH_TOOL,
         lambda uid, inp, now: handle_push_to_watch(uid, inp, now, move_service=move_service)),
        (SYNC_GARMIN_TOOL,
         lambda uid, inp, now: handle_sync_garmin(
             uid, inp, now, move_service=move_service, sense_service=sense_service)),
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
