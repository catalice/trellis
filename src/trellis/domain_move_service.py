"""
Training service — thin. The coaching judgment lives in the oracle turn (see
domain_move_claude); this only: persists the plan, reads the goal from the
second brain, OWNS THE CALENDAR (the real dates of this week — so the coach never
invents them), refreshes Garmin data, and builds structured workouts. Returns
typed data only — string formatting belongs to the tool handler.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.domain_move_models import RunLog, TrainingPlan
from trellis.domain_move_repo import TrainingRepository

_log = logging.getLogger(__name__)


class GoalReader(Protocol):
    """Training-relevant goals, read from the second brain — training stores none."""
    def list_training_goals(self, user_id: UUID) -> list: ...


class GarminWorkoutPort(Protocol):
    """Watch workouts: push, schedule, list, delete (infra_garmin.GarminDirectService)."""
    def push_workout(self, user_id: UUID, workout_json: Any) -> str: ...
    def schedule_workout(self, user_id: UUID, workout_id: str, on_date: date) -> None: ...
    def list_workouts(self, user_id: UUID, *, limit: int = 30) -> list[dict]: ...
    def delete_workout(self, user_id: UUID, workout_id: str) -> None: ...


class GarminActivityPort(Protocol):
    """Read recent activities (any type) + one activity's full detail from Garmin
    (see infra_garmin.GarminActivityReader). Recent + small only — the CSV path is
    for bulk history."""
    def recent_activities(self, user_id: UUID, *, limit: int) -> list: ...
    def activity_detail(self, user_id: UUID, activity_id: str) -> Any: ...


class GarminSyncPort(Protocol):
    """Refresh synced Garmin data (daily health + activities + details) for a user
    (see infra_garmin.GarminSyncService). Returns a summary with record counts."""
    def sync_recent(self, user_id: UUID, *, days: int) -> Any: ...


class WorkoutSpecError(ValueError):
    """The coach's workout spec couldn't be turned into a Garmin workout."""


class MoveService:
    def __init__(
        self,
        repo: TrainingRepository,
        goals: GoalReader,
        tz: tzinfo,
        *,
        garmin_push: GarminWorkoutPort | None = None,
        garmin_read: GarminActivityPort | None = None,
        garmin_sync: GarminSyncPort | None = None,
        projection=None,   # vault view with .plan_changed(user_id); best-effort
    ) -> None:
        self._repo = repo
        self._goals = goals
        self._tz = tz
        self._garmin_push = garmin_push
        self._garmin_read = garmin_read
        self._garmin_sync = garmin_sync
        self._projection = projection

    def _project_plan(self, user_id: UUID) -> None:
        """Refresh the vault's Training/Plan.md. Never raises — a failed vault
        write must not break the bot."""
        if self._projection is None:
            return
        try:
            self._projection.plan_changed(user_id)
        except Exception:
            _log.warning("plan projection failed", exc_info=True)

    # -- plan (persist what the coach authors) --------------------------------

    def get_plan(self, user_id: UUID) -> TrainingPlan | None:
        return self._repo.get(user_id)

    def save_plan(
        self,
        user_id: UUID,
        *,
        plan: dict | None = None,
        baseline: str | None = None,
        goal_id: UUID | None = None,
        replace_week: bool = False,
    ) -> TrainingPlan:
        """Upsert the coach's plan — MERGING, never destroying (12 Aug: an
        arc-note save wiped six days of stored week; the tool obeyed. Never
        again — in code, not in a prompt). arc replaces arc only when sent;
        incoming week days replace SAME-DATED days, all other stored days
        survive. replace_week=True is the only way to drop days (the Sunday
        full re-author)."""
        existing = self._repo.get(user_id)
        stored = dict(existing.plan) if existing and existing.plan else {}
        merged = dict(stored)
        if plan:
            if plan.get("arc"):
                merged["arc"] = plan["arc"]
            incoming = plan.get("week")
            if isinstance(incoming, list):
                incoming = [s for s in incoming if isinstance(s, dict) and s.get("date")]
                if replace_week:
                    merged["week"] = sorted(incoming, key=lambda x: str(x["date"]))
                else:
                    by_date = {str(x["date"]): x for x in stored.get("week", [])
                               if isinstance(x, dict) and x.get("date")}
                    for sess in incoming:
                        by_date[str(sess["date"])] = sess
                    merged["week"] = [by_date[d] for d in sorted(by_date)]
        saved = self._repo.upsert(TrainingPlan(
            user_id=user_id,
            goal_id=goal_id if goal_id is not None else (existing.goal_id if existing else None),
            baseline=baseline if baseline is not None else (existing.baseline if existing else None),
            plan=merged,
            updated_at=datetime.now(timezone.utc),
        ))
        self._project_plan(user_id)
        return saved

    def training_goals(self, user_id: UUID) -> list:
        return self._goals.list_training_goals(user_id)

    # -- completed runs (the coach plans the next from the last) ---------------

    def recent_runs(self, user_id: UUID, *, limit: int = 12) -> list[RunLog]:
        return self._repo.recent_runs(user_id, limit=limit)

    def recent_workouts(self, user_id: UUID, *, limit: int = 12) -> list[RunLog]:
        """Every sport, not just runs — the whole-body logbook."""
        return self._repo.recent_workouts(user_id, limit=limit)

    def annotate_workout(
        self, user_id: UUID, on_date: date, annotation: str,
    ) -> RunLog | None:
        """Attach the user's account of a workout — any sport — to its activity
        row (user_note, the column sync can't touch). APPENDS to their earlier
        words, never replaces. If several activities share the date, prefers the
        single run, else the latest. Returns the updated view, or None if
        nothing is recorded that day."""
        day = [w for w in self._repo.recent_workouts(user_id, limit=200)
               if w.ran_on == on_date]
        if not day:
            return None
        runs = [w for w in day if "run" in (w.activity_type or "").lower()]
        workout = runs[0] if len(runs) == 1 else day[0]
        clean = annotation.strip()
        if not clean:
            return workout
        if clean.lower() in (workout.note or "").lower():
            return workout
        words = f"{workout.user_note} — {clean}" if workout.user_note else clean
        if not self._repo.set_user_note(user_id, workout.garmin_activity_id, words):
            return None
        self._project_plan(user_id)
        from dataclasses import replace as _replace
        return _replace(workout, user_note=words, note=f"{workout.note} — {clean}")

    # -- push a structured workout to the watch (the executive-function win) ----

    def push_workout_to_watch(self, user_id: UUID, spec: dict, on_date: date) -> str:
        """Build a Garmin workout from the coach's spec and schedule it on on_date.
        Returns the workout name for confirmation. Raises WorkoutSpecError on a bad
        spec, or RuntimeError if Garmin isn't wired/connected."""
        if self._garmin_push is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        workout = build_garmin_workout(spec)               # raises WorkoutSpecError
        # A push REPLACES: same-named workouts are deleted first, so corrections
        # update the watch instead of stacking duplicates (the 4-copies mess,
        # 11 Aug). Best-effort — a failed cleanup never blocks the push.
        name = str(workout.get("workoutName") or "")
        if name:
            try:
                for w in self._garmin_push.list_workouts(user_id, limit=30):
                    if w.get("workoutName") == name and w.get("workoutId"):
                        self._garmin_push.delete_workout(user_id, str(w["workoutId"]))
            except Exception:
                _log.warning("push replace: could not clean same-named workouts", exc_info=True)
        workout_id = self._garmin_push.push_workout(user_id, workout)
        self._garmin_push.schedule_workout(user_id, workout_id, on_date)
        return str(workout.get("workoutName") or "workout")

    def sync_garmin(self, user_id: UUID, *, now: datetime, days: int = 3) -> dict:
        """Refresh this user's Garmin data: daily health + activities + details
        into the one store (the logbook reads activities directly — there is no
        separate import step since migration 017). One shared path for the
        on-demand tool and the daily background job. Raises RuntimeError only
        if the user isn't connected."""
        if self._garmin_sync is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        summary = self._garmin_sync.sync_recent(user_id, days=days)
        end = getattr(summary, "end_date", None)
        # New activities tick off planned sessions in the vault.
        self._project_plan(user_id)
        return {
            "activities": getattr(summary, "activity_records", None),
            "health_records": getattr(summary, "daily_health_records", None),
            "health_through": end.isoformat() if end is not None else None,
        }

    def review_run(self, user_id: UUID, *, which: int = 0) -> dict | None:
        """Full detail for one recent activity of ANY type (which=0 is the most
        recent — runs, strength, whatever the watch recorded): the overall summary
        + for runs a per-split breakdown (pace + HR per lap/rep). None if no
        activities. Raises RuntimeError if Garmin isn't wired/connected."""
        if self._garmin_read is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        activities = self._garmin_read.recent_activities(user_id, limit=max(which + 1, 1))
        if not activities or which >= len(activities):
            return None
        act = activities[which]
        overall: dict[str, Any] = {
            "name": getattr(act, "name", None) or "workout",
            "date": (d.isoformat() if (d := _activity_date(act, self._tz)) else None),
            "distance_km": round(act.distance_meters / 1000, 2) if getattr(act, "distance_meters", None) else None,
            "duration_min": round(act.duration_milliseconds / 60000, 1) if getattr(act, "duration_milliseconds", None) else None,
            "avg_hr": getattr(act, "average_heart_rate", None),
            "max_hr": getattr(act, "maximum_heart_rate", None),
        }
        splits: list[dict] = []
        try:
            detail = self._garmin_read.activity_detail(user_id, act.activity_id)
            splits = _extract_splits(detail)
        except Exception:
            _log.warning("review_run: detail fetch/parse failed", exc_info=True)
        return {"overall": overall, "splits": splits}

    def watch_workouts(self, user_id: UUID, *, limit: int = 15) -> list[dict]:
        """What's actually in their Garmin workout library (newest first) — so
        claims about the watch are checkable, never guessed."""
        if self._garmin_push is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        return [
            {"id": str(w.get("workoutId") or ""), "name": str(w.get("workoutName") or "")}
            for w in self._garmin_push.list_workouts(user_id, limit=limit)
        ]

    # -- calendar (Python owns real dates — the coach never does date math) ----

    def current_week(self, now: datetime) -> list[dict]:
        """This week's real dates, Monday-anchored: [{date, weekday, is_today}, ...].
        Handed to the coach so runs land on real days and day/date can't drift."""
        today = now.astimezone(self._tz).date()
        monday = today - timedelta(days=today.weekday())
        out = []
        for i in range(7):
            d = monday + timedelta(days=i)
            out.append({"date": d.isoformat(), "weekday": d.strftime("%A"), "is_today": d == today})
        return out

    def week_sessions(self, user_id: UUID) -> list[dict]:
        """The coach-authored sessions for the stored week (as-is)."""
        plan = self._repo.get(user_id)
        if plan is None:
            return []
        week = plan.plan.get("week")
        return [s for s in week if isinstance(s, dict)] if isinstance(week, list) else []

    def todays_session(self, user_id: UUID, now: datetime) -> dict | None:
        today = now.astimezone(self._tz).date().isoformat()
        for s in self.week_sessions(user_id):
            if s.get("date") == today:
                return s
        return None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_float(value: str) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.]", "", value.replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _activity_date(activity: Any, tz: tzinfo) -> date | None:
    epoch = getattr(activity, "start_time_epoch_seconds", None)
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(tz).date()
    except (ValueError, OSError, OverflowError):
        return None


_MAX_SPLITS = 60


def _extract_splits(detail: Any) -> list[dict]:
    """Pull a per-split/lap breakdown from a GarminActivityDetail, defensively —
    Garmin's split payloads vary in shape and key casing. Returns compact dicts:
    {i, type, distance_km, time, pace, avg_hr, max_hr, count?}. Empty list if
    nothing usable. count > 1 marks an AGGREGATE row (splitSummaries totals one
    row per segment TYPE) — the formatter must present those as per-type totals,
    never as a lap-by-lap timeline (audit item 26: run-walk workouts have an
    empty lap array, and the aggregates dressed up as laps read as nonsense)."""
    rows = _split_rows(detail)
    out: list[dict] = []
    for i, row in enumerate(rows[:_MAX_SPLITS], start=1):
        if not isinstance(row, dict):
            continue
        dist_m = _num(row, "distance", "totalDistance")
        secs = _num(row, "duration", "elapsedDuration", "movingDuration", "elapsedTime")
        avg_hr = _num(row, "averageHR", "averageHr", "avgHr")
        max_hr = _num(row, "maxHR", "maxHr", "maximumHr")
        speed = _num(row, "averageSpeed", "avgSpeed")  # m/s
        entry: dict[str, Any] = {"i": i}
        raw_type = row.get("type") or row.get("splitType")
        if raw_type:
            entry["type"] = _split_label(raw_type)
        count = _num(row, "noOfSplits")
        if count and count > 1:
            entry["count"] = int(count)
        if dist_m:
            entry["distance_km"] = round(dist_m / 1000, 3)
        if secs:
            entry["time"] = _mmss(secs)
        pace_secs = None
        if dist_m and secs and dist_m > 0:
            pace_secs = secs / (dist_m / 1000)
        elif speed and speed > 0:
            pace_secs = 1000 / speed
        if pace_secs:
            entry["pace"] = _mmss(pace_secs) + "/km"
        if avg_hr:
            entry["avg_hr"] = int(avg_hr)
        if max_hr:
            entry["max_hr"] = int(max_hr)
        if secs:
            entry["_secs"] = secs
        # A type label alone isn't a split — keep only rows carrying a metric.
        if any(k in entry for k in ("distance_km", "time", "pace", "avg_hr", "max_hr")):
            out.append(entry)
    # Typed payloads wrap the real segments in a whole-session container row
    # (e.g. one INTERVAL_ACTIVE spanning everything) — it duplicates the overall
    # line and wrecks the timeline. Drop any row covering ~the whole duration.
    total = sum(e.get("_secs", 0) for e in out)
    if len(out) > 3 and total:
        out = [e for e in out if e.get("_secs", 0) < 0.9 * (total - e.get("_secs", 0))]
    for n, e in enumerate(out, start=1):
        e.pop("_secs", None)
        e["i"] = n
    return out


def _split_label(raw_type: Any) -> str:
    """Garmin segment type -> a label a human reads: RWD_RUN -> run,
    INTERVAL_ACTIVE -> work, INTERVAL_REST -> recovery."""
    t = str(raw_type).upper()
    for prefix in ("RWD_", "INTERVAL_"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return {"ACTIVE": "work", "REST": "recovery"}.get(t, t.lower().replace("_", " "))


def _split_rows(detail: Any) -> list:
    """Find the list of split/lap dicts across Garmin's varying shapes.
    Preference order matters: typed splits FIRST (the chronological, labelled
    timeline — run-walk workouts often have an empty lap array), then laps,
    then splitSummaries LAST (aggregates per type, not a timeline)."""
    candidates = []
    for source in ("typed_splits", "splits", "split_summaries"):
        val = getattr(detail, source, None)
        candidates.append(val)
    raw = getattr(detail, "raw", None)
    if isinstance(raw, dict):
        candidates.extend([raw.get("typedSplits"), raw.get("splits"), raw.get("splitSummaries")])
    for val in candidates:
        rows = _as_split_list(val)
        if rows:
            return rows
    return []


def _as_split_list(val: Any) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for key in ("lapDTOs", "splitSummaries", "splits", "typedSplits"):
            inner = val.get(key)
            if isinstance(inner, list):
                return inner
    return []


def _num(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Garmin structured-workout builder — deterministic. The coach AUTHORS a simple
# spec; this turns it into valid Garmin Connect workout JSON for add_workout.
#
# Spec grammar (what the coach writes):
#   {"name": "6x400m intervals",
#    "steps": [
#      {"kind": "warmup", "duration": "10min", "note": "easy jog"},
#      {"kind": "repeat", "times": 6, "steps": [
#          {"kind": "interval", "distance": "400m", "note": "fast", "pace": "4:30-4:50"},
#          {"kind": "recovery", "duration": "90s"}]},
#      {"kind": "cooldown", "duration": "10min"}]}
#
# Per step: kind (warmup|cooldown|interval|run|recovery|rest|repeat); one of
# duration ("10min"/"90s"/"45:00"/"1h") OR distance ("400m"/"5km"/"1.5mi"),
# or neither -> open (press lap to advance). Optional note, pace ("M:SS-M:SS"
# per km), hr ("140-150" bpm). repeat needs times + nested steps.
# ---------------------------------------------------------------------------

# Step kinds -> Garmin (id, key), matching garminconnect.workout.StepType.
_STEP_KIND = {
    "warmup": (1, "warmup"), "cooldown": (2, "cooldown"),
    "interval": (3, "interval"), "run": (3, "interval"),
    "recovery": (4, "recovery"), "rest": (5, "rest"),
}
# When neither duration nor distance is given, estimate this many seconds for the
# duration total (Garmin recomputes on device; this is only for estimatedDuration).
_OPEN_STEP_SECONDS = 60
_DEFAULT_PACE_SECS_PER_KM = 360  # ~6:00/km, only for estimating distance-step time


def build_garmin_workout(spec: dict) -> dict:
    """Spec -> a VALID Garmin Connect workout dict, built on garminconnect's own
    typed models (so the format is correct by construction, pydantic-validated).
    Raises WorkoutSpecError on anything malformed — never returns junk.
    Ready for garminconnect.Garmin.upload_workout."""
    try:
        from garminconnect.workout import ExecutableStep, RepeatGroup, RunningWorkout, WorkoutSegment
    except Exception as exc:  # workout extra / pydantic missing
        raise WorkoutSpecError(f"workout builder unavailable: {exc}") from exc

    if not isinstance(spec, dict):
        raise WorkoutSpecError("workout spec must be an object")
    name = str(spec.get("name") or "Trellis workout").strip()[:80]
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkoutSpecError("workout needs a non-empty 'steps' list")

    counter = _Counter()
    total_secs = 0.0
    workout_steps = []
    for s in steps:
        model, secs = _build_step(s, counter, ExecutableStep, RepeatGroup)
        workout_steps.append(model)
        total_secs += secs

    try:
        workout = RunningWorkout(
            workoutName=name,
            estimatedDurationInSecs=int(round(total_secs)),
            workoutSegments=[WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=workout_steps,
            )],
        )
    except Exception as exc:  # pydantic validation = malformed spec
        raise WorkoutSpecError(f"couldn't build a valid workout: {exc}") from exc
    return workout.to_dict()


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _build_step(step: dict, counter: _Counter, ExecutableStep, RepeatGroup):
    """Return (model, estimated_seconds). Model is an ExecutableStep or RepeatGroup."""
    if not isinstance(step, dict):
        raise WorkoutSpecError("each step must be an object")
    kind = str(step.get("kind", "interval")).strip().lower()

    if kind == "repeat":
        times = step.get("times")
        if not isinstance(times, int) or times < 1:
            raise WorkoutSpecError("a repeat step needs a positive integer 'times'")
        nested = step.get("steps")
        if not isinstance(nested, list) or not nested:
            raise WorkoutSpecError("a repeat step needs a non-empty nested 'steps' list")
        order = counter.next()
        children = []
        child_secs = 0.0
        for s in nested:
            m, sec = _build_step(s, counter, ExecutableStep, RepeatGroup)
            children.append(m)
            child_secs += sec
        group = RepeatGroup(
            stepOrder=order,
            stepType={"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
            numberOfIterations=times,
            smartRepeat=False,
            workoutSteps=children,
        )
        return group, child_secs * times

    kind_pair = _STEP_KIND.get(kind)
    if kind_pair is None:
        raise WorkoutSpecError(f"unknown step kind '{kind}'")
    step_type_id, step_type_key = kind_pair

    end_cond, end_val, est_secs = _end_condition(step)
    target_type_id, target_key, v1, v2 = _target(step)
    fields: dict[str, Any] = {
        "stepOrder": counter.next(),
        "stepType": {"stepTypeId": step_type_id, "stepTypeKey": step_type_key, "displayOrder": step_type_id},
        "endCondition": end_cond,
        "endConditionValue": end_val,
        "targetType": {"workoutTargetTypeId": target_type_id, "workoutTargetTypeKey": target_key, "displayOrder": target_type_id},
        "targetValueOne": v1,
        "targetValueTwo": v2,
    }
    note = step.get("note")
    if note:
        fields["description"] = str(note)[:512]
    return ExecutableStep(**fields), est_secs


def _end_condition(step: dict) -> tuple[dict, float | None, float]:
    """Return (endCondition dict, endConditionValue, estimated_seconds)."""
    if step.get("duration"):
        seconds = _parse_duration_seconds(str(step["duration"]))
        if seconds is None:
            raise WorkoutSpecError(f"couldn't read duration '{step['duration']}'")
        return ({"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
                float(seconds), float(seconds))
    if step.get("distance"):
        meters = _parse_distance_meters(str(step["distance"]))
        if meters is None:
            raise WorkoutSpecError(f"couldn't read distance '{step['distance']}'")
        est = meters / 1000.0 * _DEFAULT_PACE_SECS_PER_KM
        return ({"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True},
                float(meters), est)
    # Neither given -> open step, advanced by the lap button.
    return ({"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
            None, float(_OPEN_STEP_SECONDS))


def _target(step: dict) -> tuple[int, str, float | None, float | None]:
    if step.get("pace"):
        lo, hi = _parse_pace_to_speed_range(str(step["pace"]))
        return 6, "pace.zone", lo, hi
    if step.get("hr"):
        lo, hi = _parse_range(str(step["hr"]))
        return 4, "heart.rate.zone", lo, hi
    return 1, "no.target", None, None


def _parse_duration_seconds(value: str) -> int | None:
    v = value.strip().lower()
    if ":" in v:  # "45:00" or "1:30:00"
        parts = v.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        return None
    total = 0
    matched = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|seconds)", v):
        matched = True
        n = float(num)
        if unit.startswith("h"):
            total += n * 3600
        elif unit.startswith("s"):
            total += n
        else:
            total += n * 60
    if matched:
        return int(round(total))
    if v.isdigit():  # bare number -> minutes
        return int(v) * 60
    return None


def _parse_distance_meters(value: str) -> float | None:
    v = value.strip().lower().replace(",", "")
    match = re.match(r"(\d+(?:\.\d+)?)\s*(km|k|mi|mile|miles|m)?$", v)
    if not match:
        return None
    n = float(match.group(1))
    unit = match.group(2) or "m"
    if unit in ("km", "k"):
        return n * 1000
    if unit in ("mi", "mile", "miles"):
        return n * 1609.34
    return n  # metres


def _parse_pace_to_speed_range(value: str) -> tuple[float | None, float | None]:
    """'M:SS-M:SS' per km -> (low_speed_mps, high_speed_mps). Slower pace = lower m/s."""
    parts = re.split(r"\s*[-–]\s*", value.strip())
    speeds = []
    for p in parts:
        secs = _pace_to_seconds(p)
        if secs:
            speeds.append(1000.0 / secs)
    if not speeds:
        raise WorkoutSpecError(f"couldn't read pace '{value}' (use M:SS or M:SS-M:SS per km)")
    lo, hi = min(speeds), max(speeds)
    return round(lo, 3), round(hi, 3)


def _pace_to_seconds(value: str) -> int | None:
    v = value.strip()
    if ":" not in v:
        return None
    mins, _, secs = v.partition(":")
    try:
        return int(mins) * 60 + int(secs)
    except ValueError:
        return None


def _parse_range(value: str) -> tuple[float | None, float | None]:
    parts = re.split(r"\s*[-–]\s*", value.strip())
    nums = []
    for p in parts:
        f = _parse_float(p)
        if f is not None:
            nums.append(f)
    if not nums:
        raise WorkoutSpecError(f"couldn't read range '{value}'")
    return min(nums), max(nums)
