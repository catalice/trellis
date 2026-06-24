from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from trellis.readiness_service import ReadinessSnapshot
from trellis.run_targets import RunTargetCalibration
from trellis.training_readiness import (
    TrainingAdjustment,
    TrainingReadinessAdvisor,
    TrainingReadinessRecommendation,
)
from trellis.training_history import TrainingHistoryService, TrainingHistorySummary
from trellis.training_targets import TrainingTargetFormatter
from trellis.training import (
    Intensity,
    PlanMode,
    PlanningRequest,
    SessionKind,
    SocialRunStatus,
    TrainingPlanner,
    TrainingSession,
    Weekday,
    WeeklyPlan,
    date_for_day,
)


class TrainingAction(StrEnum):
    SHOW_PLAN = "show_plan"
    CREATE_PLAN = "create_plan"
    SHOW_SESSION_DETAIL = "show_session_detail"
    REPLACE_SOCIAL_RUN = "replace_social_run"
    SET_RUN_COUNT = "set_run_count"
    UPDATE_STRENGTH_ANCHORS = "update_strength_anchors"
    EXPLAIN_PLAN = "explain_plan"
    TODAY_TRAINING = "today_training"
    RESET_PLAN = "reset_plan"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class TrainingOperation:
    action: TrainingAction
    summary: str
    detail: bool = False
    session_kind: str | None = None
    run_count: int | None = None
    strength_days: tuple[str, ...] = ()
    replacement_day: str | None = None
    replacement_time_of_day: str | None = None
    mode: str | None = None
    clarification_question: str | None = None


class TrainingRepository(Protocol):
    def save_active(self, user_id: UUID, plan: WeeklyPlan) -> WeeklyPlan: ...

    def latest_active(self, user_id: UUID, week_start: date) -> WeeklyPlan | None: ...


class TrainingProjection(Protocol):
    def write(self, plan: WeeklyPlan) -> None: ...


class ReadinessProvider(Protocol):
    def today(self, user_id: UUID, *, on_date: date) -> ReadinessSnapshot: ...


class RunTargetProvider(Protocol):
    def calibrate(self, user_id: UUID) -> RunTargetCalibration: ...


class TrainingService:
    def __init__(
        self,
        repository: TrainingRepository,
        projection: TrainingProjection,
        planner: TrainingPlanner,
        timezone: tzinfo,
        readiness: ReadinessProvider | None = None,
        readiness_advisor: TrainingReadinessAdvisor | None = None,
        run_targets: RunTargetProvider | None = None,
        target_formatter: TrainingTargetFormatter | None = None,
        training_history: TrainingHistoryService | None = None,
    ):
        self.repository = repository
        self.projection = projection
        self.planner = planner
        self.timezone = timezone
        self.readiness = readiness
        self.readiness_advisor = readiness_advisor or TrainingReadinessAdvisor()
        self.run_targets = run_targets
        self.target_formatter = target_formatter or TrainingTargetFormatter()
        self.training_history = training_history

    def get_plan_text(self, user_id: UUID, now: datetime) -> str:
        local_now = now.astimezone(self.timezone)
        week_start = self._week_start(local_now.date())
        plan = self.repository.latest_active(user_id, week_start)
        if plan is None:
            return "No active training plan this week. Call create_plan to build one."
        return self.format_plan(plan)

    def create_plan(self, user_id: UUID, now: datetime, *, mode: str | None = None) -> str:
        local_now = now.astimezone(self.timezone)
        week_start = self._target_week_start(local_now.date())
        plan_mode = PlanMode(mode) if mode in ("build", "deload", "holiday") else PlanMode.BUILD
        try:
            request, history = self._planning_request(
                user_id,
                week_start=week_start,
                mode=plan_mode,
                as_of=local_now.date(),
            )
        except Exception:
            return "Couldn't build your plan right now — the training coach is unavailable. Try again in a moment."
        plan = self.planner.plan(request)
        if history is not None:
            plan = replace(plan, rationale=plan.rationale + history.rationale)
        saved = self.repository.save_active(user_id, plan)
        self.projection.write(saved)
        return self.format_plan(saved, heading="Training plan created")

    def get_session_text(self, user_id: UUID, session_kind: str, now: datetime) -> str:
        """Return full detail for one session kind (oracle tool interface)."""
        local_now = now.astimezone(self.timezone)
        week_start = self._week_start(local_now.date())
        plan = self.repository.latest_active(user_id, week_start)
        if plan is None:
            return "No active training plan this week."
        kind = self._session_kind(session_kind)
        return self._format_session_detail(plan, kind, user_id=user_id)

    def execute_training_operation(
        self, user_id: UUID, operation, now: datetime
    ) -> str:
        """Execute a TrainingOperation directly (oracle tool interface, no AI re-interpretation)."""
        local_now = now.astimezone(self.timezone)
        week_start = self._target_week_start(local_now.date())
        return self._apply_operation(user_id, operation, week_start, local_now)

    def today_response(self, user_id: UUID, now: datetime) -> str:
        local_now = now.astimezone(self.timezone)
        week_start = self._week_start(local_now.date())
        plan = self.repository.latest_active(user_id, week_start)
        if plan is None:
            return "No active training plan for this week."
        return self._today_training_response(user_id, plan, local_now.date())

    def _apply_operation(
        self,
        user_id: UUID,
        operation: TrainingOperation,
        week_start: date,
        local_now: datetime,
    ) -> str:
        if operation.action == TrainingAction.CLARIFY:
            return operation.clarification_question or "What should I change?"

        if operation.action == TrainingAction.SHOW_PLAN:
            plan = self.repository.latest_active(user_id, week_start)
            if plan is None:
                return "No active training plan for this week."
            return self.format_plan(plan, detailed=operation.detail)

        if operation.action == TrainingAction.SHOW_SESSION_DETAIL:
            plan = self.repository.latest_active(user_id, week_start)
            if plan is None:
                return "No active training plan for this week."
            return self._format_session_detail(
                plan,
                self._session_kind(operation.session_kind),
                user_id=user_id,
            )

        if operation.action == TrainingAction.EXPLAIN_PLAN:
            plan = self.repository.latest_active(user_id, week_start)
            if plan is None:
                return "No active training plan for this week."
            return self._explain_plan(plan, operation)

        if operation.action == TrainingAction.TODAY_TRAINING:
            plan = self.repository.latest_active(user_id, week_start)
            if plan is None:
                return "No active training plan for this week."
            return self._today_training_response(user_id, plan, local_now.date())

        if operation.action == TrainingAction.RESET_PLAN:
            return "Training reset is available from the maintenance command for now."

        raise ValueError(f"Unsupported training operation: {operation.action}")

    def _explain_plan(
        self,
        plan: WeeklyPlan,
        operation: TrainingOperation,
    ) -> str:
        """Return structured plan facts for Claude to explain. Python does not write coaching text."""
        run_count = len(plan.run_sessions)
        strength_count = len([s for s in plan.sessions if s.kind == SessionKind.STRENGTH])
        hard = self._primary_hard_run(plan)
        easy_runs = [s for s in plan.run_sessions if s.kind == SessionKind.EASY_RUN]
        long_runs = [s for s in plan.run_sessions if s.kind == SessionKind.LONG_RUN]
        strength_days = [
            date_for_day(plan.week_start, s.day).strftime("%A")
            for s in plan.sessions
            if s.kind == SessionKind.STRENGTH
        ]

        lines = [
            f"Operation: explain_plan topic={operation.summary or 'general'}",
            f"Plan: week_start={plan.week_start.isoformat()} mode={plan.mode.value}",
            f"Run count: {run_count}",
            f"Strength sessions: {strength_count} ({', '.join(strength_days) if strength_days else 'none'})",
            f"Total planned time: {self._duration(plan.total_minutes)}",
        ]
        if hard is not None:
            hard_date = date_for_day(plan.week_start, hard.day)
            lines.append(f"Hard session: {hard.title} on {hard_date.strftime('%A')}")
        if easy_runs:
            easy_date = date_for_day(plan.week_start, easy_runs[0].day)
            lines.append(f"Easy run: {easy_runs[0].title} on {easy_date.strftime('%A')}")
        if long_runs:
            long_date = date_for_day(plan.week_start, long_runs[0].day)
            lines.append(f"Long run: {long_runs[0].title} on {long_date.strftime('%A')}")
        if plan.rationale:
            lines.append("Rationale: " + "; ".join(plan.rationale))
        return "\n".join(lines)

    @staticmethod
    def _primary_hard_run(plan: WeeklyPlan) -> TrainingSession | None:
        hard = [
            session
            for session in plan.run_sessions
            if session.kind in (SessionKind.HARD_RUN, SessionKind.SOCIAL_RUN)
            and session.intensity.value == "hard"
        ]
        return hard[0] if hard else None

    @staticmethod
    def _first_session(
        plan: WeeklyPlan,
        kind: SessionKind,
    ) -> TrainingSession | None:
        for session in plan.sessions:
            if session.kind == kind:
                return session
        return None

    def _planning_request(
        self,
        user_id: UUID,
        *,
        week_start: date,
        mode: PlanMode,
        as_of: date,
        social_status: SocialRunStatus | None = None,
    ) -> tuple[PlanningRequest, TrainingHistorySummary | None]:
        history = self._training_history(user_id, as_of)
        request = PlanningRequest(week_start=week_start, mode=mode)
        if social_status is not None:
            request = replace(request, social_status=social_status)
        if history is None:
            return request, None
        return (
            replace(
                request,
                long_run_minutes=history.longest_run_anchor_minutes,
                include_fourth_run=history.runs_28d >= 14,
            ),
            history,
        )

    def _training_history(
        self,
        user_id: UUID,
        as_of: date,
    ) -> TrainingHistorySummary | None:
        if self.training_history is None:
            return None
        return self.training_history.summarize(user_id, as_of=as_of)

    @staticmethod
    def _fresh_plan_identity(plan: WeeklyPlan) -> WeeklyPlan:
        return replace(
            plan,
            id=uuid4(),
            sessions=tuple(
                replace(session, id=uuid4()) for session in plan.sessions
            ),
        )

    @staticmethod
    def _plan_summary(plan: WeeklyPlan | None) -> str:
        if plan is None:
            return "No active plan."
        lines = [
            f"week_start={plan.week_start.isoformat()}",
            f"mode={plan.mode.value}",
            f"revision={plan.revision}",
        ]
        for session in plan.sessions:
            session_date = date_for_day(plan.week_start, session.day)
            start = session.start_time.strftime("%H:%M") if session.start_time else ""
            lines.append(
                (
                    f"- {session.kind.value}: {session.title}; "
                    f"{session_date.isoformat()} {start}; "
                    f"{session.total_minutes} min; {session.intensity.value}"
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _session_kind(value: str | None) -> SessionKind:
        if value == "strength":
            return SessionKind.STRENGTH
        if value is None:
            raise ValueError("session kind is required")
        return SessionKind(value)

    @staticmethod
    def format_plan(
        plan: WeeklyPlan,
        *,
        heading: str = "Training plan",
        detailed: bool = False,
    ) -> str:
        if detailed:
            return TrainingService._format_detailed_plan(plan, heading=heading)
        return TrainingService._format_compact_plan(plan, heading=heading)

    @staticmethod
    def _format_compact_plan(plan: WeeklyPlan, *, heading: str) -> str:
        lines = [
            heading,
            f"Week of {plan.week_start.isoformat()} | {plan.mode.value}",
            f"Total planned time: {TrainingService._duration(plan.total_minutes)}",
            "",
            "This week",
        ]
        for session in plan.sessions:
            session_date = date_for_day(plan.week_start, session.day)
            time_text = (
                f" {session.start_time.strftime('%H:%M')}"
                if session.start_time
                else ""
            )
            lines.append(
                (
                    f"- {session_date.strftime('%a %d %b')}{time_text}: "
                    f"{session.title} ({TrainingService._duration(session.total_minutes)}, "
                    f"{session.intensity.value})"
                )
            )
        if plan.rationale:
            lines.extend(["", "Why"])
            lines.extend(f"- {item}" for item in plan.rationale)
        lines.extend(["", "Detailed steps are saved in Calendar/Training.md."])
        return "\n".join(lines)

    @staticmethod
    def _format_detailed_plan(plan: WeeklyPlan, *, heading: str) -> str:
        lines = [
            heading,
            f"Week of {plan.week_start.isoformat()} | {plan.mode.value}",
            f"Total planned time: {TrainingService._duration(plan.total_minutes)}",
            "",
            "Sessions",
        ]
        for session in plan.sessions:
            session_date = date_for_day(plan.week_start, session.day)
            time_text = (
                f" {session.start_time.strftime('%H:%M')}"
                if session.start_time
                else ""
            )
            lines.append(
                (
                    f"- {session_date.strftime('%a %d %b')}{time_text}: "
                    f"{session.title} ({TrainingService._duration(session.total_minutes)}, "
                    f"{session.intensity.value})"
                )
            )
            for block in session.blocks:
                lines.append(
                    f"  {block.name} - {TrainingService._duration(block.duration_minutes)}"
                )
                lines.extend(f"  - {instruction}" for instruction in block.instructions)
        if plan.rationale:
            lines.extend(["", "Why"])
            lines.extend(f"- {item}" for item in plan.rationale)
        return "\n".join(lines)

    def _format_session_detail(
        self,
        plan: WeeklyPlan,
        kind: SessionKind,
        *,
        user_id: UUID | None = None,
    ) -> str:
        session = TrainingService._session(plan, kind)
        if session is None:
            return "There is no matching session in the active training plan."
        session_date = date_for_day(plan.week_start, session.day)
        time_text = (
            f" {session.start_time.strftime('%H:%M')}"
            if session.start_time
            else ""
        )
        lines = [
            f"{session.title}",
            (
                f"{session_date.strftime('%A %d %b')}{time_text} | "
                f"{TrainingService._duration(session.total_minutes)} | "
                f"{session.intensity.value}"
            ),
        ]
        target_lines = self._target_lines(user_id, session)
        for block in session.blocks:
            if target_lines and block.name == "Run":
                lines.extend(["", "Watch steps"])
                lines.extend(target_lines)
                continue
            lines.append("")
            lines.append(f"{block.name} - {TrainingService._duration(block.duration_minutes)}")
            lines.extend(f"- {instruction}" for instruction in block.instructions)
        if session.notes:
            lines.extend(["", "Notes"])
            lines.extend(f"- {note}" for note in session.notes)
        return "\n".join(lines)

    def _target_lines(
        self,
        user_id: UUID | None,
        session: TrainingSession,
    ) -> tuple[str, ...]:
        if user_id is None or self.run_targets is None:
            return ()
        if session.kind not in (
            SessionKind.HARD_RUN,
            SessionKind.EASY_RUN,
            SessionKind.LONG_RUN,
            SessionKind.SOCIAL_RUN,
        ):
            return ()
        calibration = self.run_targets.calibrate(user_id)
        return self.target_formatter.format(session, calibration).lines

    def _today_training_response(
        self,
        user_id: UUID,
        plan: WeeklyPlan,
        today: date,
    ) -> str:
        sessions = tuple(
            session
            for session in plan.sessions
            if date_for_day(plan.week_start, session.day) == today
        )
        lines = [f"Today's training - {today.isoformat()}"]
        if sessions:
            lines.extend(
                f"- {session.title} ({self._duration(session.total_minutes)}, {session.intensity.value})"
                for session in sessions
            )
        else:
            lines.append("- No training session planned today.")

        if self.readiness is None:
            lines.extend(["", "Readiness is not connected to training yet."])
            return "\n".join(lines)

        snapshot = self.readiness.today(user_id, on_date=today)
        recommendation = self.readiness_advisor.recommend(plan, snapshot, on_date=today)
        lines.extend(["", self._format_readiness_recommendation(recommendation)])
        return "\n".join(lines)

    @staticmethod
    def _priority_session(sessions: tuple[TrainingSession, ...]) -> TrainingSession:
        priority = {
            SessionKind.HARD_RUN: 0,
            SessionKind.SOCIAL_RUN: 1,
            SessionKind.LONG_RUN: 2,
            SessionKind.STRENGTH: 3,
            SessionKind.EASY_RUN: 4,
            SessionKind.MOBILITY: 5,
        }
        return sorted(sessions, key=lambda session: priority[session.kind])[0]

    @staticmethod
    def _next_hard_replacement_day(plan: WeeklyPlan, target_date: date) -> Weekday | None:
        strength_days = {
            session.day for session in plan.sessions if session.kind == SessionKind.STRENGTH
        }
        for offset in range(1, 5):
            candidate = target_date + timedelta(days=offset)
            if candidate > plan.week_start + timedelta(days=6):
                return None
            day = Weekday(candidate.weekday())
            if day not in strength_days:
                return day
        return None

    @staticmethod
    def _format_readiness_recommendation(
        recommendation: TrainingReadinessRecommendation,
    ) -> str:
        lines = [
            (
                f"Readiness: {recommendation.readiness_score}/100 "
                f"({recommendation.readiness_band.value}, "
                f"{recommendation.confidence} confidence)"
            ),
            f"Recommendation: {recommendation.action.value}",
            f"Suggested change: {recommendation.suggested_change}",
        ]
        data_lines = getattr(recommendation, "data_lines", ())
        if data_lines:
            lines.extend(["", "Data used"])
            lines.extend(f"- {item}" for item in data_lines)
        missing_metrics = getattr(recommendation, "missing_metrics", ())
        if missing_metrics:
            lines.extend(["", f"Missing: {', '.join(_metric_label(item) for item in missing_metrics)}"])
        if "self_report" in missing_metrics:
            lines.append("Missing: self_report (energy, body, life_load, soreness)")
        lines.extend(recommendation.explanation)
        return "\n".join(dict.fromkeys(lines))

    @staticmethod
    def _duration(minutes: int) -> str:
        hours, remainder = divmod(minutes, 60)
        if hours and remainder:
            return f"{hours}h {remainder}m"
        if hours:
            return f"{hours}h"
        return f"{remainder}m"

    @staticmethod
    def _session(plan: WeeklyPlan, kind: SessionKind) -> TrainingSession | None:
        return next((session for session in plan.sessions if session.kind == kind), None)

    @staticmethod
    def _long_run_minutes(plan: WeeklyPlan) -> int:
        long_run = TrainingService._session(plan, SessionKind.LONG_RUN)
        if long_run is None:
            return 60
        try:
            return long_run.block("Run").duration_minutes
        except LookupError:
            return 60

    @staticmethod
    def _week_start(day: date) -> date:
        return day - timedelta(days=day.weekday())

    @staticmethod
    def _target_week_start(day: date) -> date:
        start = TrainingService._week_start(day)
        if day.weekday() == Weekday.SUNDAY:
            return start + timedelta(days=7)
        return start


def _metric_label(name: str) -> str:
    return {
        "hrv_last_night": "HRV last night",
        "resting_heart_rate": "resting HR baseline",
        "body_battery": "body battery",
        "self_report": "self-report",
    }.get(name, name.replace("_", " "))
