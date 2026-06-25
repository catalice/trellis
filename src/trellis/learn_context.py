from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.registry import ContextLoader


class _LearningService(Protocol):
    def thread_state(self, user_id: UUID) -> list[dict]: ...


def learn_context_loader(learning_service: _LearningService) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        state = learning_service.thread_state(user_id)
        if not state:
            return None

        lines = [
            "[Learning]",
            "Teaching approach: always build the scaffold first. Start with the big picture —",
            "why this subject matters, how the major forces/eras/concepts relate to each other.",
            "Give the user somewhere to hang the details before adding them. Never open with",
            "a fun fact or anecdote. Build systematically from the foundations up.",
            "",
            "Active learning threads:",
        ]
        for item in state:
            thread = item["thread"]
            entries = item["recent_entries"]
            lines.append(f"  Thread: {thread.name} (id: {thread.id})")
            if thread.description:
                lines.append(f"    Scope: {thread.description}")
            if entries:
                covered = "; ".join(e.summary for e in entries)
                lines.append(f"    Covered so far: {covered}")
            else:
                lines.append(f"    No entries yet — start from the very beginning.")

        return "\n".join(lines)

    return loader
