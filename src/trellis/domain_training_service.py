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


class TrainingService:
    def __init__(self, repo: TrainingRepository, goals: GoalReader, tz: tzinfo) -> None:
        self._repo = repo
        self._goals = goals
        self._tz = tz

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
