from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

_MIN_EVIDENCE = 5
_EXPIRY_DAYS = 60


@dataclass(frozen=True)
class Insight:
    id: UUID
    user_id: UUID
    domain: str
    insight_type: str
    summary: str
    evidence_count: int
    confidence: float
    is_active: bool
    detected_on: date
    last_confirmed_on: date
    expires_on: date | None = None
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dismissed_reason: str | None = None
    dismissed_at: datetime | None = None
    snooze_until: date | None = None


class InsightRepository(Protocol):
    def save(self, insight: Insight) -> Insight: ...
    def list_active(self, user_id: UUID) -> list[Insight]: ...
    def deactivate_stale(self, user_id: UUID, stale_before: date) -> int: ...
    def upsert_by_type(self, insight: Insight) -> Insight: ...
    def respond(self, insight_id: UUID, action: str, note: str | None, today: date) -> None: ...


# ---------------------------------------------------------------------------
# Data summary — Python computes the facts
# ---------------------------------------------------------------------------

class DataSummariser:
    """Reads DB tables and returns a plain-text summary of recent data for Claude."""

    def __init__(
        self,
        health_repository,
        strength_session_service=None,
        workout_checkin_service=None,
        lthr: int | None = None,
    ) -> None:
        self.health_repository = health_repository
        self.strength_session_service = strength_session_service
        self.workout_checkin_service = workout_checkin_service
        self.lthr = lthr

    def summarise(self, user_id: UUID, as_of: date, *, days: int = 42) -> str | None:
        sections: list[str] = []
        since = as_of - timedelta(days=days)

        sections.append(f"Data window: {since.isoformat()} to {as_of.isoformat()} ({days} days)\n")

        # Self-reports
        self_report_stats = self._self_report_stats(user_id, as_of, days=days)
        if self_report_stats:
            sections.append(self_report_stats)

        # Strength sessions
        if self.strength_session_service is not None:
            strength_stats = self._strength_stats(user_id)
            if strength_stats:
                sections.append(strength_stats)

        # Workout checkins
        if self.workout_checkin_service is not None:
            checkin_stats = self._checkin_stats(user_id)
            if checkin_stats:
                sections.append(checkin_stats)

        # Garmin activities
        activity_stats = self._activity_stats(user_id, as_of, days=days)
        if activity_stats:
            sections.append(activity_stats)

        # Garmin daily health
        health_stats = self._daily_health_stats(user_id, as_of, days=days)
        if health_stats:
            sections.append(health_stats)

        # Return None when only the header was added (no real data)
        if len(sections) <= 1:
            return None
        return "\n".join(sections)

    def _self_report_stats(self, user_id: UUID, as_of: date, *, days: int) -> str | None:
        rows = []
        for i in range(days):
            d = as_of - timedelta(days=i)
            reports = self.health_repository.list_self_reports(user_id, d)
            if reports:
                r = reports[-1]
                rows.append({
                    "date": d,
                    "energy": r.energy_score,
                    "body": r.body_score,
                    "life_load": r.life_load_score,
                    "soreness": r.soreness_score,
                })
        if len(rows) < 3:
            return None
        avg = lambda key: _avg([r[key] for r in rows if r[key] is not None])
        lines = [f"Self-reports ({len(rows)} days logged):"]
        lines.append(f"  Average energy: {avg('energy'):.1f}/10")
        lines.append(f"  Average body: {avg('body'):.1f}/10")
        lines.append(f"  Average life load: {avg('life_load'):.1f}/10")
        if any(r["soreness"] for r in rows):
            lines.append(f"  Average soreness: {avg('soreness'):.1f}/10")

        # High-load days correlation: energy next day after high life load
        high_load_days = [r["date"] for r in rows if r["life_load"] and r["life_load"] >= 7]
        if len(high_load_days) >= 3:
            next_day_energy = []
            for d in high_load_days:
                next_rows = [r for r in rows if r["date"] == d + timedelta(days=1)]
                if next_rows and next_rows[0]["energy"]:
                    next_day_energy.append(next_rows[0]["energy"])
            if len(next_day_energy) >= 3:
                lines.append(
                    f"  After high life-load days (≥7/10): next-day energy avg {_avg(next_day_energy):.1f}/10"
                    f" ({len(next_day_energy)} instances)"
                )
        return "\n".join(lines)

    def _strength_stats(self, user_id: UUID) -> str | None:
        sessions = self.strength_session_service.list_recent(user_id, limit=20)
        if len(sessions) < 3:
            return None
        lines = [f"Strength sessions (last {len(sessions)}):"]
        days_of_week = [s.session_date.strftime("%A") for s in sessions]
        day_counts = Counter(days_of_week)
        top_days = day_counts.most_common(2)
        lines.append(f"  Most common days: {', '.join(f'{d} ({c}x)' for d, c in top_days)}")

        # Soreness after strength — cross with checkins handled in checkin_stats
        return "\n".join(lines)

    def _checkin_stats(self, user_id: UUID) -> str | None:
        checkins = self.workout_checkin_service.list_recent(user_id, limit=30)
        if len(checkins) < 3:
            return None
        lines = [f"Workout check-ins ({len(checkins)} recent):"]

        by_kind: dict[str, list] = {}
        for c in checkins:
            by_kind.setdefault(c.session_kind, []).append(c)

        for kind, cs in by_kind.items():
            efforts = [c.perceived_effort for c in cs if c.perceived_effort is not None]
            label = kind.replace("_", " ")
            if efforts:
                lines.append(f"  {label}: avg RPE {_avg(efforts):.1f} ({len(cs)} sessions)")
            else:
                lines.append(f"  {label}: {len(cs)} sessions logged")

        # Soreness patterns: check-ins with soreness notes
        sore = [c for c in checkins if c.soreness_note]
        if sore:
            lines.append(f"  Sessions with soreness noted: {len(sore)} of {len(checkins)}")
            strength_next_sore = sum(
                1 for c in sore if c.session_kind == "strength"
            )
            if strength_next_sore:
                lines.append(f"  Soreness after strength sessions: {strength_next_sore}")

        return "\n".join(lines)

    def _activity_stats(self, user_id: UUID, as_of: date, *, days: int) -> str | None:
        try:
            activities = self.health_repository.latest_activities_with_detail(user_id, limit=30)
        except Exception:
            return None
        if not activities:
            return None

        cutoff = as_of - timedelta(days=days)
        runs = []
        for a in activities:
            try:
                if not a.get("start_time_epoch_seconds"):
                    continue
                act_date = datetime.fromtimestamp(
                    int(a["start_time_epoch_seconds"]), tz=timezone.utc
                ).date()
                if act_date < cutoff:
                    continue
                if "running" not in (a.get("name") or "").lower() and \
                   "running" not in (a.get("activity_type") or "").lower():
                    continue
                if a.get("average_heart_rate") and a.get("distance_meters") and a.get("duration_milliseconds"):
                    km = a["distance_meters"] / 1000
                    mins = a["duration_milliseconds"] / 60000
                    runs.append({
                        "date": act_date,
                        "avg_hr": a["average_heart_rate"],
                        "pace_s_per_km": (mins * 60) / km if km > 0 else None,
                        "km": km,
                    })
            except Exception:
                continue

        if len(runs) < 3:
            return None

        avg_hr = _avg([r["avg_hr"] for r in runs])
        avg_pace = _avg([r["pace_s_per_km"] for r in runs if r["pace_s_per_km"]])
        pace_min, pace_s = divmod(int(avg_pace), 60)

        lines = [f"Running ({len(runs)} sessions in window):"]
        hr_line = f"  Average HR: {avg_hr:.0f} bpm"
        if self.lthr is not None:
            from trellis.run_targets import coggan_zones
            zones = coggan_zones(self.lthr)
            zone = zones.classify(int(avg_hr))
            hr_line += f" ({zone.label()})"
        lines.append(hr_line)
        lines.append(f"  Average pace: {pace_min}:{pace_s:02d}/km")

        # HR trend: first half vs second half
        mid = len(runs) // 2
        older = runs[mid:]
        newer = runs[:mid]
        if older and newer:
            older_hr = _avg([r["avg_hr"] for r in older])
            newer_hr = _avg([r["avg_hr"] for r in newer])
            diff = newer_hr - older_hr
            trend = f"trending {'up' if diff > 1 else 'down' if diff < -1 else 'stable'} ({diff:+.0f} bpm)"
            lines.append(f"  HR trend (recent vs older): {trend}")

        return "\n".join(lines)

    def _daily_health_stats(self, user_id: UUID, as_of: date, *, days: int) -> str | None:
        try:
            health = self.health_repository.latest_daily_health(user_id)
        except Exception:
            return None
        if health is None:
            return None
        lines = ["Recent Garmin health (latest available):"]
        if health.sleep_duration_minutes:
            h, m = divmod(health.sleep_duration_minutes, 60)
            lines.append(f"  Sleep: {h}h {m:02d}m")
        if health.hrv_last_night:
            lines.append(f"  HRV: {health.hrv_last_night:g} ms")
        if health.resting_heart_rate:
            lines.append(f"  Resting HR: {health.resting_heart_rate} bpm")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pattern engine — orchestrates summarise → Claude → store
# ---------------------------------------------------------------------------

_DETECTION_PROMPT = """\
You are a pattern analysis assistant for a personal training and wellbeing system.

Below is a statistical summary of data from the last 6 weeks. Your job is to identify \
genuine patterns that would be useful coaching insights. You may ONLY report patterns \
supported by the data provided. Do not extrapolate, speculate, or add context not present \
in the summary.

Rules:
- Only report a pattern if it has at least {min_evidence} data points supporting it
- State evidence counts explicitly (e.g. "in 7 of 10 instances")
- Keep each insight to one clear sentence
- Domain must be one of: training, wellbeing, productivity, cycle
- Confidence: 0.0–1.0 (use 0.5 for weak patterns, 0.8+ only for strong consistent ones)
- Return a JSON array. If no patterns meet the evidence threshold, return []

Data summary:
{summary}

Return ONLY a JSON array, no other text:
[
  {{
    "domain": "training",
    "insight_type": "recovery_pattern",
    "summary": "one sentence describing the pattern",
    "evidence_count": 8,
    "confidence": 0.75
  }}
]"""


class PatternEngine:
    def __init__(
        self,
        repository: InsightRepository,
        summariser: DataSummariser,
        anthropic_client,
        model: str,
    ) -> None:
        self.repository = repository
        self.summariser = summariser
        self.client = anthropic_client
        self.model = model

    def run(self, user_id: UUID, as_of: date) -> list[Insight]:
        summary = self.summariser.summarise(user_id, as_of)
        if not summary:
            logger.info("PatternEngine: insufficient data for user %s", user_id)
            return []

        raw_insights = self._detect(summary)
        if not raw_insights:
            return []

        saved: list[Insight] = []
        for raw in raw_insights:
            evidence_count = int(raw.get("evidence_count", 0))
            confidence = float(raw.get("confidence", 0.0))
            if evidence_count < _MIN_EVIDENCE:
                continue
            summary_text = str(raw.get("summary", "")).strip()
            if not summary_text:
                continue
            insight = Insight(
                id=uuid4(),
                user_id=user_id,
                domain=str(raw.get("domain", "general")),
                insight_type=str(raw.get("insight_type", "pattern")),
                summary=summary_text,
                evidence_count=evidence_count,
                confidence=min(1.0, max(0.0, confidence)),
                is_active=True,
                detected_on=as_of,
                last_confirmed_on=as_of,
                expires_on=as_of + timedelta(days=_EXPIRY_DAYS),
            )
            saved.append(self.repository.upsert_by_type(insight))

        self.repository.deactivate_stale(user_id, stale_before=as_of - timedelta(days=_EXPIRY_DAYS))
        return saved

    def _detect(self, summary: str) -> list[dict]:
        prompt = _DETECTION_PROMPT.format(summary=summary, min_evidence=_MIN_EVIDENCE)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Strip markdown code fences if Claude wraps in ```json
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            if not isinstance(data, list):
                return []
            return [item for item in data if isinstance(item, dict)]
        except Exception:
            logger.warning("PatternEngine: detection failed", exc_info=True)
            return []


def _avg(values: list) -> float:
    cleaned = [v for v in values if v is not None]
    return sum(cleaned) / len(cleaned) if cleaned else 0.0
