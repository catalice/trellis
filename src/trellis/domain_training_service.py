"""
Training service — thin. The coaching judgment lives in the oracle turn (see
domain_training_claude); this only: persists the plan, reads the goal from the
second brain, OWNS THE CALENDAR (the real dates of this week — so the coach never
invents them), and parses a Garmin CSV into a baseline summary. Returns typed data
only — string formatting belongs to the tool handler.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from statistics import median
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.domain_training_models import RunLog, TrainingPlan
from trellis.domain_training_repo import TrainingRepository

_log = logging.getLogger(__name__)


class GoalReader(Protocol):
    """Training-relevant goals, read from the second brain — training stores none."""
    def list_training_goals(self, user_id: UUID) -> list: ...


class GarminWorkoutPort(Protocol):
    """Push a structured workout to the watch (see infra_garmin.GarminDirectService)."""
    def push_workout(self, user_id: UUID, workout_json: Any) -> str: ...
    def schedule_workout(self, user_id: UUID, workout_id: str, on_date: date) -> None: ...


class GarminActivityPort(Protocol):
    """Read recent RUNNING activities from Garmin (see infra_garmin.GarminActivityReader).
    Recent + small only — the CSV path is for bulk history."""
    def recent_running_activities(self, user_id: UUID, *, limit: int) -> list: ...


class WorkoutSpecError(ValueError):
    """The coach's workout spec couldn't be turned into a Garmin workout."""


class TrainingService:
    def __init__(
        self,
        repo: TrainingRepository,
        goals: GoalReader,
        tz: tzinfo,
        *,
        garmin_push: GarminWorkoutPort | None = None,
        garmin_read: GarminActivityPort | None = None,
    ) -> None:
        self._repo = repo
        self._goals = goals
        self._tz = tz
        self._garmin_push = garmin_push
        self._garmin_read = garmin_read

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

    def log_run(
        self, user_id: UUID, note: str, *, now: datetime,
        ran_on: date | None = None, distance_km: float | None = None,
    ) -> RunLog:
        """Record a completed run. ran_on defaults to today (local)."""
        return self._repo.add_run(RunLog(
            id=uuid4(),
            user_id=user_id,
            ran_on=ran_on or now.astimezone(self._tz).date(),
            note=note.strip(),
            distance_km=distance_km,
            created_at=now,
        ))

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
        activities = self._garmin_read.recent_running_activities(user_id, limit=limit)
        existing = {
            (r.ran_on, round(r.distance_km, 1) if r.distance_km is not None else None)
            for r in self._repo.recent_runs(user_id, limit=200)
        }
        logged: list[RunLog] = []
        for act in activities:
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

    # -- baseline from a Garmin activity export CSV (deterministic) ------------

    def parse_garmin_csv(self, csv_text: str) -> dict:
        """Parse a Garmin Connect activity-export CSV into a compact running baseline.
        Deterministic + defensive: running rows only, tolerant of missing/renamed
        columns and unit quirks. The coach interprets the numbers; this just extracts."""
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
        except Exception:
            _log.warning("parse_garmin_csv: could not read CSV", exc_info=True)
            return {"error": "couldn't read that CSV"}

        def col(row: dict, *names: str) -> str:
            for key, val in row.items():
                if key and key.strip().lower() in names:
                    return (val or "").strip()
            return ""

        runs: list[dict] = []
        for row in rows:
            atype = col(row, "activity type").lower()
            if "run" not in atype:
                continue
            d = _parse_date(col(row, "date"))
            dist = _parse_float(col(row, "distance"))
            runs.append({
                "date": d,
                "distance": dist,
                "avg_hr": _parse_float(col(row, "avg hr")),
                "max_hr": _parse_float(col(row, "max hr")),
                "pace": col(row, "avg pace"),
            })

        dated = [r for r in runs if r["date"] is not None]
        distances = [r["distance"] for r in runs if r["distance"]]
        hrs = [r["avg_hr"] for r in runs if r["avg_hr"]]
        summary: dict[str, Any] = {"total_runs": len(runs)}
        if not runs:
            summary["note"] = "no running activities found in the file"
            return summary

        if dated:
            first, last = min(r["date"] for r in dated), max(r["date"] for r in dated)
            summary["date_range"] = f"{first.isoformat()} to {last.isoformat()}"
            weeks = max(1, ((last - first).days / 7) or 1)
            if distances:
                summary["avg_km_per_week"] = round(sum(distances) / weeks, 1)
        if distances:
            summary["total_km"] = round(sum(distances), 1)
            summary["longest_run_km"] = round(max(distances), 1)
            summary["typical_run_km"] = round(median(distances), 1)
        if hrs:
            summary["avg_hr"] = round(sum(hrs) / len(hrs))
            summary["max_avg_hr"] = round(max(hrs))
        summary["unit_note"] = "distances as given by the export (km or mi per the account)"
        return summary


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


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    head = value.strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def _activity_date(activity: Any, tz: tzinfo) -> date | None:
    epoch = getattr(activity, "start_time_epoch_seconds", None)
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(tz).date()
    except (ValueError, OSError, OverflowError):
        return None


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

_SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running"}
_STEP_TYPES = {
    "warmup": 1, "cooldown": 2, "interval": 3, "run": 3,
    "recovery": 4, "rest": 5, "repeat": 6,
}
_STEP_TYPE_KEYS = {1: "warmup", 2: "cooldown", 3: "interval", 4: "recovery", 5: "rest", 6: "repeat"}


def build_garmin_workout(spec: dict) -> dict:
    """Spec -> Garmin Connect workout JSON (dict) for garminconnect.add_workout.
    Raises WorkoutSpecError on anything malformed — never returns junk."""
    if not isinstance(spec, dict):
        raise WorkoutSpecError("workout spec must be an object")
    name = str(spec.get("name") or "Trellis workout").strip()[:80]
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkoutSpecError("workout needs a non-empty 'steps' list")

    counter = _Counter()
    workout_steps = [_build_step(s, counter) for s in steps]
    return {
        "sportType": dict(_SPORT_RUNNING),
        "workoutName": name,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(_SPORT_RUNNING),
            "workoutSteps": workout_steps,
        }],
    }


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _build_step(step: dict, counter: _Counter) -> dict:
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
        child_steps = [_build_step(s, counter) for s in nested]
        return {
            "type": "RepeatGroupDTO",
            "stepOrder": order,
            "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
            "numberOfIterations": times,
            "smartRepeat": False,
            "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
            "endConditionValue": times,
            "workoutSteps": child_steps,
        }

    step_type_id = _STEP_TYPES.get(kind)
    if step_type_id is None:
        raise WorkoutSpecError(f"unknown step kind '{kind}'")

    end_cond, end_val = _end_condition(step)
    target_type_id, target_key, v1, v2 = _target(step)
    out: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": counter.next(),
        "stepType": {"stepTypeId": step_type_id, "stepTypeKey": _STEP_TYPE_KEYS[step_type_id]},
        "endCondition": end_cond,
        "endConditionValue": end_val,
        "targetType": {"workoutTargetTypeId": target_type_id, "workoutTargetTypeKey": target_key},
        "targetValueOne": v1,
        "targetValueTwo": v2,
    }
    note = step.get("note")
    if note:
        out["description"] = str(note)[:512]
    return out


def _end_condition(step: dict) -> tuple[dict, float | None]:
    if step.get("duration"):
        seconds = _parse_duration_seconds(str(step["duration"]))
        if seconds is None:
            raise WorkoutSpecError(f"couldn't read duration '{step['duration']}'")
        return {"conditionTypeId": 2, "conditionTypeKey": "time"}, float(seconds)
    if step.get("distance"):
        meters = _parse_distance_meters(str(step["distance"]))
        if meters is None:
            raise WorkoutSpecError(f"couldn't read distance '{step['distance']}'")
        return {"conditionTypeId": 3, "conditionTypeKey": "distance"}, float(meters)
    # Neither given -> open step, advanced by the lap button.
    return {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}, None


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
