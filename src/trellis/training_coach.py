from __future__ import annotations

from datetime import date
from uuid import UUID

from trellis.training_arc import ArcRepository, TrainingArc


class TrainingCoach:
    """Thin wrapper around arc_repository for arc display. No Claude calls."""

    def __init__(self, arc_repository: ArcRepository, **_ignored) -> None:
        self.arc_repository = arc_repository

    def get_arc(self, user_id: UUID) -> TrainingArc | None:
        return self.arc_repository.get_active(user_id)

    def format_arc_for_display(self, user_id: UUID, today: date) -> str:
        from trellis.training_tool import _format_arc_for_display
        arc = self.get_arc(user_id)
        if arc is None:
            return "No training arc yet. Ask me to build one."
        return _format_arc_for_display(arc, today)
