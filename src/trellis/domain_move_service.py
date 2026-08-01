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
    """Push a structured workout to the watch (see infra_garmin.GarminDirectService)."""
    def push_workout(self, user_id: UUID, workout_json: Any) -> str: ...
    def schedule_workout(self, user_id: UUID, workout_id: str, on_date: date) -> None: ...


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
    ) -> None:
        self._repo = repo
        self._goals = goals
        self._tz = tz
        self._garmin_push = garmin_push
        self._garmin_read = garmin_read
        self._garmin_sync = garmin_sync

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
    ) -> TrainingPlan:
        """Upsert the coach's plan. Any field left None keeps its stored value, so
        the coach can update just the week, just the baseline, etc."""
        existing = self._repo.get(user_id)
        return self._repo.upsert(TrainingPlan(
            user_id=user_id,
            goal_id=goal_id if goal_id is not None else (existing.goal_id if existing else None),
            baseline=baseline if baseline is not None else (existing.baseline if existing else None),
            plan=plan if plan is not None else (existing.plan if existing else {}),
            updated_at=datetime.now(timezone.utc),
        ))

    def training_goals(self, user_id: UUID) -> list:
        return self._goals.list_training_goals(user_id)

    # -- completed runs (the coach plans the next from the last) ---------------

    def recent_runs(self, user_id: UUID, *, limit: int = 12) -> list[RunLog]:
        return self._repo.recent_runs(user_id, limit=limit)

    # -- push a structured workout to the watch (the executive-function win) ----

    def push_workout_to_watch(self, user_id: UUID, spec: dict, on_date: date) -> str:
        """Build a Garmin workout from the coach's spec and schedule it on on_date.
        Returns the workout name for confirmation. Raises WorkoutSpecError on a bad
        spec, or RuntimeError if Garmin isn't wired/connected."""
        if self._garmin_push is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        workout = build_garmin_workout(spec)               # raises WorkoutSpecError
        workout_id = self._garmin_push.push_workout(user_id, workout)
        self._garmin_push.schedule_workout(user_id, workout_id, on_date)
        return str(workout.get("workoutName") or "workout")

    def import_recent_runs(self, user_id: UUID, *, now: datetime, limit: int = 20) -> list[RunLog]:
        """Pull recent RUNNING activities from Garmin and log any new ones. Recent +
        small (not bulk history — that's the CSV). Dedupes against already-logged runs
        by (date, ~distance). Raises RuntimeError if Garmin isn't wired/connected."""
        if self._garmin_read is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        activities = self._garmin_read.recent_activities(user_id, limit=limit)
        existing = {
            (r.ran_on, round(r.distance_km, 1) if r.distance_km is not None else None)
            for r in self._repo.recent_runs(user_id, limit=200)
        }
        logged: list[RunLog] = []
        for act in activities:
            # The run LOG is runs-only by definition (it feeds baseline/planning);
            # the reader itself is unfiltered so review_run can see everything.
            if "run" not in (getattr(act, "activity_type", "") or "").lower():
                continue
            ran_on = _activity_date(act, self._tz)
            if ran_on is None:
                continue
            dist_km = round(act.distance_meters / 1000, 2) if getattr(act, "distance_meters", None) else None
            key = (ran_on, round(dist_km, 1) if dist_km is not None else None)
            if key in existing:
                continue
            existing.add(key)
            note = (getattr(act, "name", None) or "run").strip()
            if getattr(act, "average_heart_rate", None):
                note += f" (avg HR {act.average_heart_rate})"
            logged.append(self._repo.add_run(RunLog(
                id=uuid4(), user_id=user_id, ran_on=ran_on,
                note=note, distance_km=dist_km, created_at=now,
            )))
        return logged

    def sync_garmin(self, user_id: UUID, *, now: datetime, days: int = 3) -> dict:
        """Refresh this user's Garmin data: recent runs into the training log AND
        recent daily health/activities/details into the health store. One shared
        path for the on-demand tool and the daily background job. Best-effort per
        part; raises RuntimeError only if the user isn't connected at all."""
        if self._garmin_read is None and self._garmin_sync is None:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        result: dict[str, Any] = {"new_runs": 0, "health_records": None, "health_through": None}
        connection_errors = 0
        attempts = 0

        if self._garmin_sync is not None:
            attempts += 1
            try:
                summary = self._garmin_sync.sync_recent(user_id, days=days)
                result["health_records"] = getattr(summary, "daily_health_records", None)
                end = getattr(summary, "end_date", None)
                result["health_through"] = end.isoformat() if end is not None else None
            except RuntimeError:
                connection_errors += 1
            except Exception:
                _log.warning("sync_garmin: health sync failed", exc_info=True)

        if self._garmin_read is not None:
            attempts += 1
            try:
                new = self.import_recent_runs(user_id, now=now, limit=20)
                result["new_runs"] = len(new)
            except RuntimeError:
                connection_errors += 1
            except Exception:
                _log.warning("sync_garmin: run import failed", exc_info=True)

        # If every part we tried failed because the user isn't connected, surface it.
        if attempts and connection_errors == attempts:
            raise RuntimeError("Garmin isn't set up. Connect it with /garmin_setup first.")
        return result

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


_MAX_SPLITS = 40


def _extract_splits(detail: Any) -> list[dict]:
    """Pull a per-split/lap breakdown from a GarminActivityDetail, defensively —
    Garmin's split payloads vary in shape and key casing. Returns compact dicts:
    {i, distance_km, time, pace, avg_hr, max_hr}. Empty list if nothing usable."""
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
        if len(entry) > 1:  # more than just the index
            out.append(entry)
    return out


def _split_rows(detail: Any) -> list:
    """Find the list of split/lap dicts across Garmin's varying shapes."""
    candidates = []
    for source in ("split_summaries", "splits", "typed_splits"):
        val = getattr(detail, source, None)
        candidates.append(val)
    raw = getattr(detail, "raw", None)
    if isinstance(raw, dict):
        candidates.extend([raw.get("splitSummaries"), raw.get("splits"), raw.get("typedSplits")])
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
