"""
Training domain models.

Re-exports everything from existing training.py + training_arc.py so that
new domain files import from one place. Delete this comment and the old
source files once the migration is complete.
"""
from trellis.training import (
    Intensity,
    PlanMode,
    PlanningRequest,
    SessionBlock,
    SessionKind,
    SocialRunStatus,
    TrainingGoal,
    TrainingPlanner,
    TrainingSession,
    Weekday,
    WeeklyPlan,
    date_for_day,
)
from trellis.training_arc import ArcPhase, ArcRepository, TrainingArc

TRAINING_SIGNALS: list[str] = [
    "run", "running", "training", "plan", "session", "race",
    "pace", "interval", "tempo", "hard run", "easy run", "long run",
    "social run", "strength", "pt", "personal training",
    "arc", "phase", "marathon", "5k", "10k", "half marathon",
    "deload", "build", "taper", "week review",
    "mobility", "adapt", "readiness", "km",
]

__all__ = [
    "Intensity", "PlanMode", "PlanningRequest", "SessionBlock",
    "SessionKind", "SocialRunStatus", "TrainingGoal", "TrainingPlanner",
    "TrainingSession", "Weekday", "WeeklyPlan", "date_for_day",
    "ArcPhase", "ArcRepository", "TrainingArc",
    "TRAINING_SIGNALS",
]
