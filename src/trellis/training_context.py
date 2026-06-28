"""
Context loader for the training domain.

Assembles the training-relevant section of the system prompt: Garmin health,
readiness, active plan, recent activities, workout check-ins, strength sessions.

Usage in main.py:
    registry.add_domain("training", training_context_loader(...), ...)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Callable, Protocol
from uuid import UUID

from trellis.registry import ContextLoader

_log = logging.getLogger(__name__)


# --- Protocols (structural — no imports from service files) -----------------

class _HealthRepo(Protocol):
    def latest_daily_health(self, user_id: UUID): ...
    def latest_activities_with_detail(self, user_id: UUID, *, limit: int) -> list[dict]: ...
    def list_self_reports(self, user_id: UUID, observed_on: date) -> tuple: ...


class _ReadinessProvider(Protocol):
    def today(self, user_id: UUID, *, on_date: date, prefetched_reports: tuple | None = None): ...


class _TrainingRepo(Protocol):
    def latest_active(self, user_id: UUID, week_start: date): ...


class _WorkoutCheckinService(Protocol):
    def list_recent(self, user_id: UUID, *, limit: int) -> list: ...


class _StrengthSessionService(Protocol):
    def list_recent(self, user_id: UUID, *, limit: int) -> list: ...


class _ArcRepository(Protocol):
    def get_active(self, user_id: UUID): ...


class _AnchorService(Protocol):
    def summary_for_coach(self, user_id: UUID) -> str | None: ...


class _TrainingHistoryService(Protocol):
    def summarize(self, user_id: UUID, *, as_of: date): ...


class _CycleService(Protocol):
    def current_phase(self, user_id: UUID, today: date) -> str | None: ...


class _GarminSyncService(Protocol):
    def sync_if_stale(self, user_id: UUID, *, stale_after_minutes: int = 10, days: int = 2) -> bool: ...


class _CompletionService(Protocol):
    def refresh(self, user_id: UUID, week_start: date, as_of: date) -> None: ...
    def summary(self, user_id: UUID, week_start: date) -> str: ...


_COACHING_INSTRUCTIONS = """\
Coaching framework — follow these when handling training requests:

WEEK PLAN: When the user asks to plan the week, generate sessions and call \
save_week_plan with the full sessions array. Do not call adjust_training for this.
  BUILD: include easy run, hard run (threshold for build phase, VO2max for sharpen), \
long run, mobility. Use arc phase targets and readiness to calibrate.
  DELOAD: easy running and mobility only — no hard sessions, no long run.
  Day rules: avoid anchor days, no consecutive run days, long run needs rest or \
easy movement the day before. Do not generate strength sessions.

SESSION CONTENT: Every session needs real blocks with specific instructions — \
not placeholders. Vary activation drills each week (A-skips, B-skips, pogos, \
leg swings, high knees, lateral shuffles). Effort language: conversational (easy), \
hard but sustainable (threshold/moderate), very hard (VO2max/hard).

TRAINING ARC: When asked to build or regenerate the arc, generate phases then \
call save_training_arc. Start from today, build toward race date, include recovery.
  Phases: Aerobic Base → Build → Sharpen → Taper → Recovery.
  Each phase needs name, focus, start/end dates, weekly_runs, long_run_minutes, \
intensity, notes.

READINESS ADAPTATION: keep (score ≥70), reduce (50–69, same type lighter), \
swap (hard session, score <50 — replace with easy today), rest (very low or illness).
  To apply adaptation: generate the adapted session and call apply_readiness_adaptation.\
"""


# --- Factory ----------------------------------------------------------------

def training_context_loader(
    health_repository: _HealthRepo,
    readiness_provider: _ReadinessProvider,
    training_repository: _TrainingRepo,
    timezone: tzinfo,
    workout_checkin_service: _WorkoutCheckinService | None = None,
    strength_session_service: _StrengthSessionService | None = None,
    arc_repository: _ArcRepository | None = None,
    anchor_service: _AnchorService | None = None,
    training_history_service: _TrainingHistoryService | None = None,
    cycle_service: _CycleService | None = None,
    preferences_repository=None,
    garmin_sync_service: _GarminSyncService | None = None,
    completion_service: _CompletionService | None = None,
) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        local_now = now.astimezone(timezone)
        today = local_now.date()
        parts: list[str] = []

        last_week_start = (today - timedelta(days=today.weekday())) - timedelta(days=7)
        last_week_end = last_week_start + timedelta(days=6)
        week_start_local = today - timedelta(days=today.weekday())

        if garmin_sync_service is not None:
            try:
                garmin_sync_service.sync_if_stale(user_id, stale_after_minutes=10, days=2)
            except Exception:
                _log.warning("training_context: garmin sync failed", exc_info=True)

        if completion_service is not None:
            try:
                completion_service.refresh(user_id, week_start_local, today)
                completion_service.refresh(user_id, last_week_start, last_week_end)
            except Exception:
                _log.warning("training_context: completion refresh failed", exc_info=True)

        try:
            health = health_repository.latest_daily_health(user_id)
            if health is not None:
                parts.append(_format_health(health))
        except Exception:
            _log.warning("training_context: health load failed", exc_info=True)

        try:
            activities = health_repository.latest_activities_with_detail(user_id, limit=10)
            if activities:
                parts.append(_format_recent_activities(activities, today))
        except Exception:
            _log.warning("training_context: activities load failed", exc_info=True)

        self_reports: tuple | None = None
        try:
            self_reports = health_repository.list_self_reports(user_id, today)
            line = _format_self_report(self_reports)
            parts.append(line if line else "Self-report (today): not yet logged")
        except Exception:
            _log.warning("training_context: self-report load failed", exc_info=True)

        try:
            readiness = readiness_provider.today(
                user_id, on_date=today, prefetched_reports=self_reports
            )
            if readiness is not None and readiness.score > 0:
                parts.append(
                    f"Readiness: {readiness.score}/100 "
                    f"({readiness.band.value}, {readiness.confidence} confidence)"
                )
        except Exception:
            _log.warning("training_context: readiness load failed", exc_info=True)

        try:
            plan = training_repository.latest_active(user_id, week_start_local)
            if plan is not None:
                parts.append(_format_plan_compact(plan, today))
        except Exception:
            _log.warning("training_context: plan load failed", exc_info=True)

        if workout_checkin_service is not None:
            try:
                checkins = workout_checkin_service.list_recent(user_id, limit=5)
                if checkins:
                    parts.append(_format_workout_checkins(checkins, today))
            except Exception:
                _log.warning("training_context: checkins load failed", exc_info=True)

        if strength_session_service is not None:
            try:
                sessions = strength_session_service.list_recent(user_id, limit=6)
                if sessions:
                    parts.append(_format_strength_sessions(sessions, today))
            except Exception:
                _log.warning("training_context: strength sessions load failed", exc_info=True)

        if arc_repository is not None:
            try:
                arc = arc_repository.get_active(user_id)
                if arc is not None:
                    parts.append("[Training arc]\n" + arc.summary_for_coach(today))
                else:
                    parts.append("[Training arc]\nNo arc set. Call save_training_arc to build one.")
            except Exception:
                _log.warning("training_context: arc load failed", exc_info=True)

        if anchor_service is not None:
            try:
                anchors = anchor_service.summary_for_coach(user_id)
                if anchors:
                    parts.append(f"Training anchors (fixed — do not generate sessions for these):\n{anchors}")
            except Exception:
                _log.warning("training_context: anchors load failed", exc_info=True)

        if training_history_service is not None:
            try:
                history = training_history_service.summarize(user_id, as_of=today)
                if history is not None:
                    hist_lines = [
                        f"Training history (28d): {history.runs_28d} runs, "
                        f"{history.distance_28d_km:.0f}km, {history.minutes_28d} min total",
                    ]
                    if history.longest_run_84d_km and history.longest_run_84d_minutes:
                        hist_lines.append(
                            f"Longest run (84d): {history.longest_run_84d_km:.1f}km "
                            f"/ {history.longest_run_84d_minutes} min"
                        )
                    hist_lines.append(f"Long run anchor: {history.longest_run_anchor_minutes} min")
                    if history.rationale:
                        hist_lines.extend(f"- {r}" for r in history.rationale)
                    parts.append("\n".join(hist_lines))
            except Exception:
                _log.warning("training_context: history load failed", exc_info=True)

        if cycle_service is not None:
            try:
                cycle = cycle_service.current_phase(user_id, today)
                if cycle:
                    parts.append(f"Cycle phase: {cycle}")
            except Exception:
                _log.warning("training_context: cycle phase load failed", exc_info=True)

        if completion_service is not None:
            try:
                last_week_summary = completion_service.summary(user_id, last_week_start)
                if last_week_summary:
                    parts.append(
                        f"Last week ({last_week_start.strftime('%-d %b')}–{last_week_end.strftime('%-d %b')}):\n{last_week_summary}"
                    )
            except Exception:
                _log.warning("training_context: last week summary failed", exc_info=True)

        parts.append(_COACHING_INSTRUCTIONS)

        if preferences_repository is not None:
            prefs = preferences_repository.get(user_id, "training")
            if prefs:
                parts.append(f"[Your training preferences]\n{prefs}")

        if not parts:
            return None
        return "[Training]\n" + "\n\n".join(parts)

    return loader


# --- Formatting helpers (training-specific) ---------------------------------

def _day_label(d: date, today: date) -> str:
    delta = (today - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _format_health(record) -> str:
    parts = [f"Garmin ({record.observed_on.isoformat()}):"]
    if record.sleep_duration_minutes is not None:
        h, m = divmod(record.sleep_duration_minutes, 60)
        sleep_str = f"{h}h {m:02d}m"
        if record.sleep_score is not None:
            sleep_str += f" (score {record.sleep_score})"
        parts.append(f"sleep {sleep_str}")
    bb = record.body_battery_end or record.body_battery_maximum
    if bb is not None:
        parts.append(f"body battery {bb}")
    if record.resting_heart_rate is not None:
        parts.append(f"resting HR {record.resting_heart_rate} bpm")
    if record.hrv_last_night is not None:
        parts.append(f"HRV {record.hrv_last_night:g} ms")
    if record.average_stress is not None:
        parts.append(f"stress {record.average_stress}")
    return ", ".join(parts)


def _format_self_report(reports: tuple) -> str | None:
    if not reports:
        return None
    latest = reports[-1]
    parts = []
    if latest.energy_score is not None:
        parts.append(f"energy {latest.energy_score}/10")
    if latest.body_score is not None:
        parts.append(f"body {latest.body_score}/10")
    if latest.life_load_score is not None:
        parts.append(f"life load {latest.life_load_score}/10")
    if latest.soreness_score is not None:
        parts.append(f"soreness {latest.soreness_score}/10")
    if not parts:
        return None
    return "Self-report (today): " + ", ".join(parts)


def _format_plan_compact(plan, today: date) -> str:
    from trellis.training import date_for_day
    lines = [f"Training this week ({plan.mode.value}, revision {plan.revision}):"]
    for session in plan.sessions:
        session_date = date_for_day(plan.week_start, session.day)
        marker = " ← TODAY" if session_date == today else ""
        lines.append(
            f"  {session_date.strftime('%a %d %b')}: "
            f"{session.title} ({session.total_minutes}m, {session.intensity.value}){marker}"
        )
    return "\n".join(lines)


def _format_recent_activities(activities: list[dict], today: date) -> str:
    lines = ["Recent Garmin activities:"]
    for act in activities:
        if not act.get("start_time_epoch_seconds"):
            continue
        act_date = datetime.fromtimestamp(
            act["start_time_epoch_seconds"], tz=timezone.utc
        ).date()
        day_label = _day_label(act_date, today)

        stats: list[str] = []
        distance = act.get("distance_meters")
        duration_ms = act.get("duration_milliseconds")
        if distance and duration_ms:
            km = distance / 1000
            mins = duration_ms / 60000
            pace_sec = (duration_ms / 1000) / (distance / 1000)
            pace_min, pace_s = divmod(int(pace_sec), 60)
            stats.append(f"{km:.2f} km in {mins:.0f}m ({pace_min}:{pace_s:02d}/km)")
        elif duration_ms:
            stats.append(f"{duration_ms / 60000:.0f}m")
        avg_hr = act.get("average_heart_rate")
        max_hr = act.get("maximum_heart_rate")
        if avg_hr:
            hr = f"avg HR {avg_hr}"
            if max_hr:
                hr += f" / max {max_hr}"
            stats.append(hr)
        if act.get("calories"):
            stats.append(f"{act['calories']} kcal")
        elev = act.get("elevation_gain_meters")
        if elev and elev >= 20:
            stats.append(f"↑{elev:.0f}m")

        summary = f"  {day_label} — {act['name']}"
        if stats:
            summary += f" ({', '.join(stats)})"
        lines.append(summary)
        lines.extend(_format_interval_splits(act.get("typed_splits")))

    return "\n".join(lines)


def _format_interval_splits(typed_splits: dict | None) -> list[str]:
    if not typed_splits or not isinstance(typed_splits, dict):
        return []
    splits = typed_splits.get("splits")
    if not splits or not isinstance(splits, list):
        return []
    active = [s for s in splits if s.get("type") == "INTERVAL_ACTIVE"]
    if not active:
        return []
    lines = []
    for i, s in enumerate(active, 1):
        dur = s.get("duration", 0)
        dist = s.get("distance")
        hr = s.get("averageHR") or s.get("averageHeartRate")
        speed = s.get("averageSpeed")
        parts = [f"    Interval {i}: {int(dur // 60)}:{int(dur % 60):02d}"]
        if dist:
            parts.append(f"{dist:.0f}m")
        if speed and speed > 0:
            pace_sec = 1 / speed
            pace_min, pace_s = divmod(int(pace_sec), 60)
            parts.append(f"{pace_min}:{pace_s:02d}/km")
        if hr:
            parts.append(f"HR {int(hr)}")
        lines.append(" ".join(parts))
    return lines


def _format_workout_checkins(checkins: list, today: date) -> str:
    lines = ["Recent workout check-ins:"]
    for c in checkins:
        day_label = _day_label(c.checked_in_on, today)
        kind = c.session_kind.replace("_", " ")
        parts = [f"  {day_label} — {kind}"]
        if c.perceived_effort is not None:
            parts.append(f"RPE {c.perceived_effort}/10")
        if c.feel_note:
            parts.append(c.feel_note)
        if c.soreness_note:
            parts.append(f"soreness: {c.soreness_note}")
        lines.append(
            ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]
        )
    return "\n".join(lines)


def _format_strength_sessions(sessions: list, today: date) -> str:
    lines = ["Strength sessions (recent):"]
    for s in sessions:
        day_label = _day_label(s.session_date, today)
        phase = f" [{s.program_phase}]" if s.program_phase else ""
        exercises = ", ".join(e.display() for e in s.exercises)
        line = f"  {day_label}{phase}"
        if exercises:
            line += f": {exercises}"
        if s.notes:
            line += f" — {s.notes}"
        lines.append(line)
    return "\n".join(lines)
