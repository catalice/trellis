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

        lines = ["Active learning threads:"]
        for item in state:
            thread = item["thread"]
            entries = item["recent_entries"]
            if entries:
                covered = "; ".join(e.summary for e in entries)
                lines.append(f"  [{thread.name}] Covered so far: {covered}")
            else:
                lines.append(f"  [{thread.name}] No entries yet — start fresh from the beginning.")
            if thread.description:
                lines.append(f"    Scope: {thread.description}")

        return "[Learning]\n" + "\n".join(lines)

    return loader
