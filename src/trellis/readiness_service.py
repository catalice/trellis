from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from trellis.health import GarminDailyHealthRecord, SelfHealthReport
from trellis.readiness import (
    DailyReadinessInput,
    ReadinessBand,
    ReadinessCalculator,
    ReadinessContribution,
    ReadinessResult,
    SelfReport,
)


class ReadinessHealthRepository(Protocol):
    def get_daily_health(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> GarminDailyHealthRecord | None:
        ...

    def latest_daily_health(self, user_id: UUID) -> GarminDailyHealthRecord | None:
        ...

    def resting_heart_rate_baseline(
        self,
        user_id: UUID,
        *,
        before: date,
        days: int = 60,
    ) -> int | None:
        ...

    def list_self_reports(
        self,
        user_id: UUID,
        observed_on: date,
    ) -> tuple[SelfHealthReport, ...]:
        ...


@dataclass(frozen=True)
class ReadinessSnapshot:
    user_id: UUID
    requested_on: date
    source_health_date: date | None
    used_latest_health_fallback: bool
    score: int
    band: ReadinessBand
    confidence: str
    contributions: tuple[ReadinessContribution, ...]
    rationale: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    data_lines: tuple[str, ...]
    self_report_id: UUID | None = None

    @property
    def has_garmin_data(self) -> bool:
        return self.source_health_date is not None


class ReadinessService:
    def __init__(
        self,
        repository: ReadinessHealthRepository,
        *,
        calculator: ReadinessCalculator | None = None,
    ) -> None:
        self.repository = repository
        self.calculator = calculator or ReadinessCalculator()

    def today(
        self,
        user_id: UUID,
        *,
        on_date: date,
        prefetched_reports: tuple | None = None,
    ) -> ReadinessSnapshot:
        health = self.repository.get_daily_health(user_id, on_date)
        used_latest_fallback = False
        if health is None:
            health = self.repository.latest_daily_health(user_id)
            used_latest_fallback = health is not None

        self_report = self._latest_self_report(user_id, on_date, prefetched=prefetched_reports)
        history = self._recent_history(user_id, on_date)
        result = self.calculator.assess(
            self._input(user_id, on_date, health, self_report),
            history=history,
        )

        return self._snapshot(
            user_id=user_id,
            requested_on=on_date,
            health=health,
            used_latest_fallback=used_latest_fallback,
            self_report=self_report,
            result=result,
        )

    def _recent_history(
        self,
        user_id: UUID,
        on_date: date,
    ) -> tuple[ReadinessResult, ...]:
        from datetime import timedelta

        results: list[ReadinessResult] = []
        for days_ago in (1, 2, 3):
            past_date = on_date - timedelta(days=days_ago)
            past_health = self.repository.get_daily_health(user_id, past_date)
            if past_health is None:
                continue
            past_report = self._latest_self_report(user_id, past_date)
            result = self.calculator.assess(
                self._input(user_id, past_date, past_health, past_report)
            )
            results.append(result)
        return tuple(results)

    def _input(
        self,
        user_id: UUID,
        on_date: date,
        health: GarminDailyHealthRecord | None,
        self_report: SelfHealthReport | None,
    ) -> DailyReadinessInput:
        if health is None:
            return DailyReadinessInput(
                date=on_date,
                sleep_duration_minutes=self_report.sleep_minutes if self_report else None,
                self_report=_self_report(self_report),
            )

        return DailyReadinessInput.from_normalized_health(
            health,
            on_date=on_date,
            resting_heart_rate_baseline=self.repository.resting_heart_rate_baseline(
                user_id,
                before=on_date,
            ),
            self_report=_self_report(self_report),
        )

    def _latest_self_report(
        self,
        user_id: UUID,
        on_date: date,
        prefetched: tuple | None = None,
    ) -> SelfHealthReport | None:
        reports = prefetched if prefetched is not None else self.repository.list_self_reports(user_id, on_date)
        reports_with_signal = tuple(
            report
            for report in reports
            if report.energy_score is not None
            or report.body_score is not None
            or report.life_load_score is not None
            or report.sleep_minutes is not None
        )
        return reports_with_signal[-1] if reports_with_signal else None

    @staticmethod
    def _snapshot(
        *,
        user_id: UUID,
        requested_on: date,
        health: GarminDailyHealthRecord | None,
        used_latest_fallback: bool,
        self_report: SelfHealthReport | None,
        result: ReadinessResult,
    ) -> ReadinessSnapshot:
        return ReadinessSnapshot(
            user_id=user_id,
            requested_on=requested_on,
            source_health_date=health.observed_on if health else None,
            used_latest_health_fallback=used_latest_fallback,
            score=result.score,
            band=result.band,
            confidence=result.confidence,
            contributions=result.contributions,
            rationale=result.rationale,
            missing_metrics=result.missing_metrics,
            data_lines=_data_lines(
                requested_on=requested_on,
                health=health,
                used_latest_fallback=used_latest_fallback,
                self_report=self_report,
            ),
            self_report_id=self_report.id if self_report else None,
        )


def _self_report(report: SelfHealthReport | None) -> SelfReport | None:
    if report is None:
        return None
    if (
        report.energy_score is None
        and report.body_score is None
        and report.life_load_score is None
    ):
        return None
    return SelfReport(
        energy=report.energy_score or 5,
        body=report.body_score or 5,
        life_load=report.life_load_score or 5,
        soreness=report.soreness_score or 3,
    )


def _data_lines(
    *,
    requested_on: date,
    health: GarminDailyHealthRecord | None,
    used_latest_fallback: bool,
    self_report: SelfHealthReport | None,
) -> tuple[str, ...]:
    lines: list[str] = []
    if health is None:
        lines.append("Garmin: no daily health data stored.")
    else:
        source = health.observed_on.isoformat()
        suffix = " (latest available fallback)" if used_latest_fallback else ""
        lines.append(f"Garmin source: {source}{suffix}.")
        _append_metric(lines, "Sleep", _sleep_text(health))
        _append_metric(lines, "Body battery", _int_text(health.body_battery_end or health.body_battery_maximum))
        _append_metric(lines, "Resting HR", _bpm_text(health.resting_heart_rate))
        _append_metric(lines, "HRV", _hrv_text(health))
        _append_metric(lines, "Stress", _int_text(health.average_stress))

    if self_report is None:
        lines.append("Self-report: missing.")
    else:
        bits = []
        if self_report.energy_score is not None:
            bits.append(f"energy {self_report.energy_score}/10")
        if self_report.body_score is not None:
            bits.append(f"body {self_report.body_score}/10")
        if self_report.life_load_score is not None:
            bits.append(f"life load {self_report.life_load_score}/10")
        if self_report.sleep_minutes is not None:
            bits.append(f"reported sleep {self_report.sleep_minutes // 60}h {self_report.sleep_minutes % 60}m")
        lines.append("Self-report: " + (", ".join(bits) if bits else "present but no scoring signal."))
    return tuple(lines)


def _append_metric(lines: list[str], label: str, value: str | None) -> None:
    if value is not None:
        lines.append(f"{label}: {value}.")


def _sleep_text(health: GarminDailyHealthRecord) -> str | None:
    parts = []
    if health.sleep_duration_minutes is not None:
        parts.append(f"{health.sleep_duration_minutes // 60}h {health.sleep_duration_minutes % 60}m")
    if health.sleep_score is not None:
        parts.append(f"score {health.sleep_score}")
    return ", ".join(parts) if parts else None


def _int_text(value: int | None) -> str | None:
    return str(value) if value is not None else None


def _bpm_text(value: int | None) -> str | None:
    return f"{value} bpm" if value is not None else None


def _hrv_text(health: GarminDailyHealthRecord) -> str | None:
    if health.hrv_last_night is None:
        return None
    return f"last night {health.hrv_last_night:g}"
