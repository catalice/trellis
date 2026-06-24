"""
Training domain — all Claude calls.

Thin wrapper that exposes TrainingCoach under the new module naming convention.
Prompts live as module-level constants in training_coach.py (source of truth until
that file is fully merged here).
"""
from trellis.training_coach import CoachDecision, TrainingCoach

__all__ = ["TrainingCoach", "CoachDecision"]
