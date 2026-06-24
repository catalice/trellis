from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Any


class ReadinessBand(StrEnum):
    LOW = "low"
    STEADY = "steady"
    READY = "ready"
    STRONG = "strong"


@dataclass(frozen=True)
class SelfReport:
    energy: int
    body: int
    life_load: int
    soreness: int

    def __post_init__(self) -> None:
        for name, value in (
            ("energy", self.energy),
            ("body", self.body),
            ("life_load", self.life_load),
            ("soreness", self.soreness),
        ):
            if not 1 <= value <= 10:
                raise ValueError(f"{name} must be between 1 and 10")


@dataclass(frozen=True)
class DailyReadinessInput:
    date: date
    sleep_duration_minutes: int | None = None
    sleep_score: int | None = None
    body_battery: int | None = None
    resting_heart_rate: int | None = None
    resting_heart_rate_baseline: int | None = None
    hrv_last_night: float | None = None
    hrv_baseline: float | None = None
    average_stress: int | None = None
    self_report: SelfReport | None = None

    @classmethod
    def from_normalized_health(
        cls,
        health: Any,
        *,
        on_date: date,
        resting_heart_rate_baseline: int | None = None,
        hrv_baseline: float | None = None,
        self_report: SelfReport | None = None,
    ) -> "DailyReadinessInput":
        return cls(
            date=on_date,
            sleep_duration_minutes=getattr(health, "sleep_duration_minutes", None),
            sleep_score=getattr(health, "sleep_score", None),
            body_battery=getattr(health, "body_battery_end", None)
            or getattr(health, "body_battery_maximum", None),
            resting_heart_rate=getattr(health, "resting_heart_rate", None),
            resting_heart_rate_baseline=resting_heart_rate_baseline,
            hrv_last_night=getattr(health, "hrv_last_night", None),
            hrv_baseline=hrv_baseline,
            average_stress=getattr(health, "average_stress", None),
            self_report=self_report,
        )


@dataclass(frozen=True)
class ReadinessContribution:
    name: str
    points: int
    rationale: str


@dataclass(frozen=True)
class ReadinessResult:
    date: date
    score: int
    band: ReadinessBand
    confidence: str
    contributions: tuple[ReadinessContribution, ...]
    rationale: tuple[str, ...]
    missing_metrics: tuple[str, ...]

    def with_score_for_test(self, score: int) -> "ReadinessResult":
        return replace(self, score=score, band=_band(score))


class ReadinessCalculator:
    def assess(
        self,
        inputs: DailyReadinessInput,
        *,
        history: tuple[ReadinessResult, ...] = (),
    ) -> ReadinessResult:
        contributions: list[ReadinessContribution] = []
        rationale: list[str] = []
        missing: list[str] = []

        self._sleep(inputs, contributions, rationale, missing)
        self._body_battery(inputs, contributions, rationale, missing)
        self._heart_rate(inputs, contributions, missing)
        self._hrv(inputs, contributions, missing)
        self._stress(inputs, contributions, missing)
        self._self_report(inputs, contributions, rationale, missing)
        self._trend(history, contributions)

        score = 65 + sum(item.points for item in contributions)
        if inputs.self_report is None and self._objective_metric_count(inputs) >= 5:
            contributions.append(
                ReadinessContribution(
                    "objective_data",
                    9,
                    "Garmin signals are broad enough for a high-confidence estimate",
                )
            )
            score += 9
        if any(item.points <= -10 for item in contributions) and score >= 90:
            score = 89
        score = max(0, min(100, score))
        confidence = self._confidence(missing)
        if confidence == "low":
            rationale.append("Readiness is usable but low-confidence")
        if any(item.points < -8 for item in contributions) and score >= 75:
            rationale.append("No single poor metric is allowed to decide the day")

        return ReadinessResult(
            date=inputs.date,
            score=score,
            band=_band(score),
            confidence=confidence,
            contributions=tuple(contributions),
            rationale=tuple(rationale),
            missing_metrics=tuple(missing),
        )

    @staticmethod
    def _sleep(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        rationale: list[str],
        missing: list[str],
    ) -> None:
        if inputs.sleep_score is None and inputs.sleep_duration_minutes is None:
            missing.append("sleep")
            return
        points = 0
        if inputs.sleep_score is not None:
            if inputs.sleep_score >= 85:
                points += 9
                rationale.append("Sleep is strong")
            elif inputs.sleep_score >= 75:
                points += 5
            elif inputs.sleep_score < 60:
                points -= 8
        if inputs.sleep_duration_minutes is not None:
            if inputs.sleep_duration_minutes >= 450:
                points += 4
            elif inputs.sleep_duration_minutes < 360:
                points -= 6
        contributions.append(ReadinessContribution("sleep", points, "Sleep recovery signal"))

    @staticmethod
    def _body_battery(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        rationale: list[str],
        missing: list[str],
    ) -> None:
        if inputs.body_battery is None:
            missing.append("body_battery")
            return
        if inputs.body_battery >= 80:
            points = 8
        elif inputs.body_battery >= 70:
            points = 6
        elif inputs.body_battery >= 60:
            points = 6
        elif inputs.body_battery < 40:
            points = -10
        else:
            points = -4
        contributions.append(
            ReadinessContribution("body_battery", points, "Garmin body battery")
        )

    @staticmethod
    def _heart_rate(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        missing: list[str],
    ) -> None:
        if inputs.resting_heart_rate is None or inputs.resting_heart_rate_baseline is None:
            missing.append("resting_heart_rate")
            return
        delta = inputs.resting_heart_rate - inputs.resting_heart_rate_baseline
        points = 4 if delta <= -2 else 2 if delta <= 1 else 0 if delta <= 3 else -4 if delta <= 5 else -8
        contributions.append(
            ReadinessContribution("resting_heart_rate", points, "Resting HR versus baseline")
        )

    @staticmethod
    def _hrv(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        missing: list[str],
    ) -> None:
        if inputs.hrv_last_night is None:
            missing.append("hrv_last_night")
            return
        if inputs.hrv_last_night >= 55:
            points = 7
        elif inputs.hrv_last_night >= 45:
            points = 3
        elif inputs.hrv_last_night >= 35:
            points = 0
        else:
            points = -10
        contributions.append(ReadinessContribution("hrv", points, "Last-night HRV"))

    @staticmethod
    def _stress(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        missing: list[str],
    ) -> None:
        if inputs.average_stress is None:
            missing.append("stress")
            return
        points = 4 if inputs.average_stress < 30 else 3 if inputs.average_stress < 45 else -5
        contributions.append(ReadinessContribution("stress", points, "Average stress"))

    @staticmethod
    def _self_report(
        inputs: DailyReadinessInput,
        contributions: list[ReadinessContribution],
        rationale: list[str],
        missing: list[str],
    ) -> None:
        report = inputs.self_report
        if report is None:
            missing.append("self_report")
            return
        points = 0
        points += round((report.energy - 5) * 1.5)
        points += round((report.body - 5) * 1.5)
        points -= round((report.life_load - 5) * 1.1)
        points -= round((report.soreness - 3) * 1.2)
        contributions.append(ReadinessContribution("self_report", points, "Subjective state"))

    @staticmethod
    def _trend(
        history: tuple[ReadinessResult, ...],
        contributions: list[ReadinessContribution],
    ) -> None:
        recent = tuple(result.score for result in sorted(history, key=lambda item: item.date)[-3:])
        if len(recent) < 3:
            return
        average = sum(recent) / len(recent)
        if average < 55:
            contributions.append(
                ReadinessContribution(
                    "trend",
                    -6,
                    f"Last 3 days average {average:.1f}",
                )
            )
        elif average > 80:
            contributions.append(
                ReadinessContribution(
                    "trend",
                    3,
                    f"Last 3 days average {average:.1f}",
                )
            )

    @staticmethod
    def _confidence(missing: list[str]) -> str:
        available_count = 6 - len(
            [name for name in missing if name in {"sleep", "body_battery", "resting_heart_rate", "hrv_last_night", "stress", "self_report"}]
        )
        if available_count >= 5:
            return "high"
        if available_count >= 3:
            return "medium"
        return "low"

    @staticmethod
    def _objective_metric_count(inputs: DailyReadinessInput) -> int:
        return sum(
            value is not None
            for value in (
                inputs.sleep_score,
                inputs.body_battery,
                inputs.resting_heart_rate,
                inputs.hrv_last_night,
                inputs.average_stress,
            )
        )


def _band(score: int) -> ReadinessBand:
    if score >= 90:
        return ReadinessBand.STRONG
    if score >= 75:
        return ReadinessBand.READY
    if score >= 55:
        return ReadinessBand.STEADY
    return ReadinessBand.LOW
